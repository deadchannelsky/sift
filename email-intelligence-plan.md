# Email Intelligence Extraction Project Plan

## Overview
Extract Q4 2025 email conversations, enrich with semantic AI analysis via Ollama, produce quarterly project/stakeholder summary with explainability.

**Architecture**: Local PST processing → SQLite store → Ollama API (via SSH tunnel) → Enriched JSON → Reporting

---

## Phase 1: Data Ingestion & Normalization

### 1.1 PST Parsing
- **Input**: Exported PST file
- **Process**:
  - Use `pypff` to walk folder structure
  - Extract messages for Q4 2025 (Oct 1 - Dec 31)
  - Filter by conversation_topic > 3 messages threshold
  - Store to SQLite normalized schema
- **Output**: `messages.db` with tables: `messages`, `conversations`, `attachments`
- **Key fields**:
  - `msg_id` (unique)
  - `conversation_topic` (threading key)
  - `subject`, `body_snippet` (first 500 chars)
  - `sender_email`, `recipients` (CSV)
  - `delivery_date`
  - `message_class` (e.g., IPM.Schedule.Meeting.Request)
  - `has_ics_attachment` (boolean)

### 1.2 Data Validation
- Count messages ingested
- Identify conversations with > 3 messages
- Log any parsing errors to separate file
- Output: Summary stats, error log

---

## Phase 2: Ollama Integration & Enrichment

### 2.1 SSH Tunnel & API Configuration
- **Setup**: User manually creates SSH tunnel
  ```bash
  ssh -L 11434:localhost:11434 user@remote-box
  ```
- **Code reads**: `config.json` with:
  ```json
  {
    "ollama_url": "http://localhost:11434",
    "ollama_model": "mistral:7b",
    "batch_size": 5,
    "timeout": 30
  }
  ```

### 2.2 Enrichment Pipeline
Four parallel extraction tasks per message batch:

**Task A: Project Extraction**
- Prompt: Extract project names/initiatives with reasoning
- Output: `projects[]` with confidence, evidence chain

**Task B: Stakeholder Detection**
- Prompt: Identify key people, inferred roles, interaction patterns
- Output: `stakeholders[]` with confidence, role inference

**Task C: Time Allocation Weighting**
- Prompt: Estimate strategic importance tier
- Output: `importance_tier` (critical/execution/overhead/fyi/noise) + weight multiplier

**Task D: Meeting Confidence**
- Prompt: Detect real meetings vs. discussion threads
- Output: `is_meeting` (bool), `meeting_confidence`, `inferred_attendees`

### 2.3 Progress & Error Handling
- Progress bar: X/Y messages processed
- Real-time logging of:
  - Message ID, conversation topic being processed
  - API response time
  - Confidence scores
  - Any parsing errors (fallback to low confidence + raw text)
- Retry logic: 3 attempts per message, exponential backoff

---

## Phase 3: Aggregation & Clustering

### 3.1 Project Clustering
- Group identical/near-identical project extractions via embedding similarity
- Merge confidence scores across related conversations
- Track evidence chains per canonical project name

### 3.2 Stakeholder Graph
- Build person-to-project relationships
- Role consolidation (if Alice is detected as "PM" and "Product Manager," unify)
- Interaction frequency & influence scoring

### 3.3 Temporal Analysis
- For each project: active date range, engagement pattern (continuous vs. sporadic)
- Quarterly engagement ratio (% of 91 days actively engaged)

---

## Phase 4: Reporting & Output

### 4.1 Output Formats
1. **JSON (full detail)**: `enriched_conversations.json`
   - All raw extractions with confidence & reasoning
   - Useful for iteration and debugging

2. **Markdown (human-readable)**: `Q4_2025_Summary.md`
   - High-confidence projects with stakeholders
   - Medium-confidence flagged for review
   - Low-confidence/noise section
   - Temporal patterns

3. **CSV (tabular)**: `projects_summary.csv`
   - Project name, confidence, email count, meeting count, key stakeholders, date range

### 4.2 Confidence-Based Filtering
- **≥0.80**: Auto-include, mark as "CONFIDENT"
- **0.50–0.79**: Flag as "REVIEW_REQUIRED"
- **<0.50**: Separate "UNCERTAIN" section

---

## Phase 5 (Optional): Service Architecture

### If implementing remote server service:

**Option A: Simple Flask service on remote box**
- Endpoint: `POST /enrich` → accepts message batch → returns enriched JSON
- Runs Ollama API locally, no SSH tunnel needed
- Access from local machine via SSH-authenticated HTTP

**Option B: Full microservice stack**
- Remote service: FastAPI + SQLAlchemy (normalized store)
- Local CLI/web client: queries remote service, displays results
- Useful if: you want persistent enriched data on remote box, or multiple local clients

**Recommendation**: Start with option A (keep it simple, test the prompt quality first), migrate to B if you iterate frequently.

---

## File Structure

```
email-intelligence/
├── config.json                    # Ollama URL, model, batch size
├── claude.md                      # This plan + API reference
├── json_schema.md                 # Full JSON schema for outputs
├── src/
│   ├── main.py                    # Entry point
│   ├── pst_parser.py              # Phase 1: PST ingestion
│   ├── ollama_client.py           # Phase 2: API + batching
│   ├── enrichment.py              # Phase 2: Task prompts & parsing
│   ├── aggregator.py              # Phase 3: Clustering & merging
│   ├── reporter.py                # Phase 4: Output generation
│   └── utils.py                   # Logging, error handling, progress
├── prompts/                       # Separate files for each LLM task
│   ├── task_a_projects.txt
│   ├── task_b_stakeholders.txt
│   ├── task_c_importance.txt
│   └── task_d_meetings.txt
├── data/
│   ├── your_file.pst              # Input
│   ├── messages.db                # SQLite store (auto-generated)
│   ├── enriched_conversations.json # Output
│   ├── Q4_2025_Summary.md         # Output
│   └── projects_summary.csv       # Output
└── logs/
    └── enrichment.log
```

---

## Key Decisions & Trade-offs

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | SQLite | Local, no setup, sufficient for single user |
| Batching | 5 messages per API call | Balance latency vs. throughput |
| Model | Mistral 7B (user configurable) | Good reasoning, 7B fits vRAM, fast |
| Confidence threshold | 0.80 auto-include | Conservative; fewer false positives |
| Explainability | JSON chains + reasoning | Auditable, iterable, not black-box |
| Remote access | SSH tunnel + HTTP | Secure, no auth complexity for MVP |

---

## Success Criteria

- [ ] PST parses without errors, ≥90% message capture
- [ ] Ollama integration stable (no dropped requests, <10% retry rate)
- [ ] Project extraction: ≥10 unique projects identified, confidence distribution reasonable
- [ ] Stakeholder graph: key team members appear in ≥2 projects
- [ ] Temporal patterns identifiable (e.g., "Q4 front-loaded," "steady engagement")
- [ ] Manual review of medium-confidence items validates ≥70% accuracy
- [ ] Reporting is readable and drives Q4 retrospective narrative

---

## Next Steps (For Claude Code)

1. **Create schema file** (`json_schema.md`) with full JSON structures
2. **Stub out main.py** with config loading + phase entry points
3. **Implement Phase 1** (PST → SQLite) with error handling
4. **Test Phase 2** with 10 messages, iterate on prompts
5. **Build Phase 3 & 4** once Phase 2 is tuned
6. **Manual validation** on 20–30 high/medium/low confidence items
7. **Iterate prompts** based on findings
8. **Generate reports** for Q4 retrospective

---

## Notes for Claude Code Session

- You have `slylinux` RTX 5070 with Ollama running; tunnel is pre-established
- Mistral 7B or Llama 2 13B are good model choices (test both if time permits)
- Logging should be verbose during Phase 2 (we're validating LLM output quality)
- Plan for 1–2 hour runtime end-to-end (depending on message count and batch size)
