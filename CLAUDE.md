# Sift - Email Intelligence Extraction

## What This Does

Analyzes a email archives (PST format) dor a given date range using local Ollama LLM to extract work intelligence:

- **Projects & Initiatives**: Identifies project names with confidence scores and evidence chains
- **Stakeholder Analysis**: Detects key people, infers roles (PM, Engineer, etc.), interaction patterns
- **Time Allocation**: Estimates strategic importance tiers (Critical/Execution/Coordination/FYI/Noise)
- **Meeting Detection**: Distinguishes calendar events from discussions, infers meeting metadata

Output: Quarterly summary report with project timelines, stakeholder graphs, and temporal engagement patterns.

---

## Architecture

**Data Flow**:
```
PST Export → SQLite → Ollama API (SSH tunnel) → Enriched JSON → Reports (JSON/Markdown/CSV)
```

**Key Components**:
- **PST Parser**: Walks Outlook archive, filters given date range, extracts conversations with 3+ messages
- **Ollama Client**: Batched API calls to `mistral:7b` on remote RTX 5070 (via `localhost:11434`)
- **Enrichment Pipeline**: Four parallel LLM tasks per message (projects, stakeholders, importance, meetings)
- **Aggregator**: Clusters projects by similarity, merges confidence scores, builds stakeholder graph
- **Reporter**: Generates human-readable Markdown + machine-readable JSON/CSV

**Storage**: SQLite (local, single-user, no setup required)

---

## Key Constraints

1. **locally exposed ollama instance**: Ollama API accessed via localhost:11434 
2. **Explainability**: All extractions include confidence scores + reasoning chains (auditable)
3. **Fault Tolerance**: Handle Ollama timeouts gracefully (3 retries, exponential backoff, continue on failure)
4. **Conversation Threshold**: Only analyze email threads with 3+ messages (filter noise)
5. **Date Range**: make date range selectable for filtering from given .pst

---

## Design Preferences

**Prompts as Configuration**:
- LLM prompts stored in `prompts/*.json` (editable, version-controlled)
- Each prompt file includes: task description, template, model params (temperature, tokens)
- Config file (`config.json`) references prompts by ID for easy swapping
- CLI supports runtime prompt override for A/B testing

**Progressive Detail**:
- Phase 1: PST → SQLite (get data pipeline solid first)
- Phase 2: Ollama integration (test prompt quality on small sample)
- Phase 3: Full enrichment (batch process all messages)
- Phase 4: Aggregation + reporting (cluster, deduplicate, generate summaries)

**Logging & Observability**:
- Verbose progress logs: message ID, API latency, confidence scores
- Track errors separately: API timeouts, JSON parse failures, low-confidence extractions
- Report statistics: avg confidence, high/medium/low distribution, enrichment success rate

**Fail-Safe Behavior**:
- If Ollama API fails: Store message with error flag, continue processing (don't crash)
- If JSON parsing fails: Return low-confidence fallback, log raw response
- If prompt file missing: Load default prompt, warn user

---

## Configuration

**Main Config** (`config.json`):
- Ollama connection details (URL, model, timeout, retries)
- Prompt strategy selection (reference by prompt ID)
- Processing parameters (batch size, date range, message threshold)
- Output formats (JSON, Markdown, CSV)

See `config.json` for template.

**Prompt Format** (`prompts/task_*.json`):
- `prompt_id`: Unique identifier for selection
- `version`: Semantic versioning for tracking iterations
- `model_params`: Temperature, token limit, stop sequences
- `prompt_template`: Prompt text with `{variable}` placeholders
- `example_output`: Sample JSON response for validation

---

## Prompts as Experiments

Prompts live in separate JSON files, allowing easy iteration:
- **task_a_projects_v1.json**: Extract project names (conservative, high confidence)
- **task_a_projects_v2_aggressive.json**: Alternative strategy (lower threshold)
- **task_b_stakeholders_v1.json**: Detect roles and interaction patterns
- **task_c_importance_v1.json**: Assess work tiers (critical/execution/overhead/fyi/noise)
- **task_d_meetings_v1.json**: Identify real meetings vs. discussions

Edit prompt text in JSON files without touching code. Config file or CLI flag selects which version to use.

---

## CLI Usage (Future)

```bash
# List available prompts
sift list-prompts --task project_extraction

# Run enrichment with default config
sift enrich --config config.json

# Override specific prompt for testing
sift enrich --prompt-project task_a_projects_v2_aggressive

# Generate reports from enriched data
sift report --format markdown --output Q4_2025_Summary.md
```

---

## Reference Documents

- **email-intelligence-plan.md**: Detailed phase-by-phase architecture & success criteria
- **json_schema.md**: Complete data structure specifications for all phases
- **docs/ollama_api_reference.md**: Ollama API technical details (connection, endpoints, batching)
- **prompts/*.json**: LLM task prompts with examples and metadata

---

## Development Workflow

1. **Phase 1**: Implement PST parser, validate message extraction, test with sample file
2. **Phase 2**: Integrate Ollama API, test prompts on 5-10 messages, iterate prompt quality
3. **Phase 3**: Run full enrichment pipeline, monitor errors, validate confidence distributions
4. **Phase 4**: Build aggregation + clustering, generate reports, manual review of results
5. **Iterate**: Refine prompts based on accuracy review, adjust confidence thresholds

---

## Success Criteria

- PST parses without errors, ≥90% message capture rate
- Ollama integration stable (<10% retry rate, no dropped requests)
- Project extraction: ≥10 unique projects identified with reasonable confidence distribution
- Stakeholder graph: Key team members appear in ≥2 projects
- Manual review validates ≥70% accuracy on medium-confidence items
- Reports drive Q4 retrospective narrative effectively
