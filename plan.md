# Sift - Development Plan

## Project Overview

**Sift** extracts work intelligence from Q4 2025 email archives (PST format) using local Ollama LLM to identify:
- Projects & initiatives with confidence scores
- Stakeholder roles & interaction patterns
- Work importance tiers (Critical/Execution/Coordination/FYI/Noise)
- Meeting detection & time allocation estimates

Output: Quarterly summary report with project timelines, stakeholder graphs, and engagement patterns.

---

## Tech Stack

### Backend (Python)
- **Framework**: FastAPI (async, JSON-native, lightweight)
- **Database**: SQLite (local, single-user)
- **Port**: 5000
- **Key Libraries**: pypff (PST parsing), pandas (data processing), requests (HTTP), uvicorn (ASGI)

### Frontend (Node.js)
- **Framework**: Express (minimal, static file serving)
- **Client**: Vanilla JavaScript (fetch API)
- **Styling**: Tailwind CSS (CDN, no build step)
- **Port**: 3000

### Infrastructure (Split Architecture with SSH Tunnel)
- **Frontend**: Runs on Windows laptop (localhost:3000)
- **Backend**: Runs on Linux server, tunneled to Windows (localhost:5000 via `ssh -L 5000:localhost:5000`)
- **Ollama**: Runs on Linux server, tunneled to Windows (localhost:11434 via `ssh -L 11434:localhost:11434`)
- **Data Flow**:
  ```
  Windows Laptop:
    Browser (localhost:3000)
         ↓ (fetch API)
    localhost:5000 ← SSH tunnel
         ↓
  Linux Server:
    FastAPI Backend (localhost:5000)
         ↓ (local HTTP)
    Ollama API (localhost:11434)
         ↓ (batched LLM calls)
    SQLite (data/messages.db on server)
  ```

**Why this architecture?**
- pypff (PST parsing) only works on Linux (Windows C++ build issues)
- Backend on server = no compilation issues
- Frontend tunneled back to Windows = single dev environment on laptop
- Ollama stays local to backend (no latency)

---

## Architecture

### Data Processing Pipeline

```
1. PST Input (file on laptop)
   ↓
2. PST Parser (Python)
   - Extract Q4 2025 messages
   - Filter: conversations with 3+ messages
   - Store to SQLite
   ↓
3. Enrichment (4 parallel LLM tasks per message)
   - Task A: Project extraction (prompts/task_a_projects_v1.json)
   - Task B: Stakeholder detection (prompts/task_b_stakeholders_v1.json)
   - Task C: Importance assessment (prompts/task_c_importance_v1.json)
   - Task D: Meeting detection (prompts/task_d_meetings_v1.json)
   ↓
4. Aggregation & Clustering
   - Group identical/similar projects
   - Merge confidence scores
   - Build stakeholder graph
   ↓
5. Reporting
   - JSON (full detail)
   - Markdown (human-readable)
   - CSV (tabular)
   ↓
6. Frontend Display (Web UI)
   - Projects table (name, confidence, message count)
   - Stakeholders table (name, role, projects)
   - Timeline view (engagement pattern)
   - Report viewer
```

### File Structure

```
sift/
├── CLAUDE.md                              # High-level project overview
├── email-intelligence-plan.md             # Detailed phase breakdown (legacy)
├── json_schema.md                         # Data structure specifications
├── config.json                            # Runtime configuration
├── plan.md                                # THIS FILE - development plan
├── README.md                              # Quick start guide (TBD)
│
├── prompts/                               # LLM prompt templates (JSON)
│   ├── task_a_projects_v1.json
│   ├── task_b_stakeholders_v1.json
│   ├── task_c_importance_v1.json
│   └── task_d_meetings_v1.json
│
├── docs/                                  # Technical documentation
│   └── ollama_api_reference.md
│
├── backend/                               # Python FastAPI application
│   ├── requirements.txt                   # Python dependencies
│   ├── main.py                            # FastAPI entry point
│   ├── app/
│   │   ├── __init__.py
│   │   ├── models.py                      # SQLAlchemy ORM models
│   │   ├── pst_parser.py                  # PST file parsing
│   │   ├── ollama_client.py               # Ollama API wrapper
│   │   ├── prompt_manager.py              # Load/manage prompt JSON files
│   │   ├── enrichment.py                  # Apply 4 LLM tasks
│   │   ├── aggregator.py                  # Clustering & merging
│   │   ├── reporter.py                    # Generate reports
│   │   └── utils.py                       # Logging, helpers, error handling
│   └── logs/
│       └── enrichment.log                 # Processing logs
│
├── frontend/                              # Node.js Express + vanilla JS
│   ├── package.json
│   ├── server.js                          # Express app (serves static files)
│   └── public/
│       ├── index.html                     # Single-page app HTML
│       ├── styles.css                     # Tailwind-based styling
│       └── app.js                         # Vanilla JavaScript (DOM, fetch)
│
├── data/                                  # Runtime data directory
│   ├── your_file.pst                      # Input (user-provided)
│   ├── messages.db                        # SQLite database (auto-generated)
│   └── outputs/
│       ├── enriched.json                  # Full enriched data
│       ├── Q4_2025_Summary.md             # Human-readable report
│       └── projects_summary.csv           # Tabular summary
│
└── .gitignore                             # Exclude data/, logs/, .env
```

---

## Phases & Tasks

### Phase 1: Backend Foundation (Est. 2-3 days)
**Goal**: Get PST parsing → SQLite working with FastAPI scaffolding

- [ ] Create `backend/` directory structure
- [ ] Write `requirements.txt` with core dependencies
- [ ] Implement `app/models.py` (SQLAlchemy schema: messages, conversations, attachments)
- [ ] Implement `app/pst_parser.py` (pypff → SQLite, date filtering)
- [ ] Implement `app/utils.py` (logging, progress tracking)
- [ ] Create FastAPI app entry point (`main.py`)
- [ ] Add `/parse` endpoint (accept PST file, queue job)
- [ ] Add `/status` endpoint (check progress)
- [ ] Test with sample PST file
- [ ] Verify SQLite database structure

**Deliverables**:
- FastAPI server running on port 5000
- PST file parsed to SQLite with 90%+ message capture
- Logging working (enrichment.log)

---

### Phase 2: Ollama Integration & Enrichment (Est. 1-2 days)
**Goal**: Connect to Ollama, implement 4 LLM extraction tasks

- [ ] Implement `app/ollama_client.py` (API wrapper, retry logic)
- [ ] Implement `app/prompt_manager.py` (load JSON prompts, validate structure)
- [ ] Implement `app/enrichment.py` (apply 4 tasks: projects, stakeholders, importance, meetings)
- [ ] Add batch processing (5 messages at a time)
- [ ] Test on 10 sample messages
- [ ] Refine prompts in `prompts/` based on output quality
- [ ] Add `/enrich` endpoint to FastAPI (start enrichment, return job ID)
- [ ] Add error handling & fallback (low-confidence extraction on failure)
- [ ] Test retry logic (simulate Ollama timeout)

**Deliverables**:
- Ollama integration stable (<10% retry rate)
- 4 LLM tasks working with confidence scores
- Enrichment logs showing task performance

---

### Phase 3: Full Processing & Aggregation (Est. 1 day)
**Goal**: Run enrichment on full dataset, cluster & merge results

- [ ] Implement `app/aggregator.py` (project clustering, stakeholder deduplication)
- [ ] Implement `app/reporter.py` (generate JSON, Markdown, CSV reports)
- [ ] Add `/results` endpoint (return enriched data)
- [ ] Run full PST enrichment (all 300-500 messages)
- [ ] Validate confidence distributions
- [ ] Test aggregation logic
- [ ] Generate sample reports

**Deliverables**:
- Full enriched dataset in SQLite
- JSON, Markdown, CSV reports in `data/outputs/`
- Aggregation working (projects deduplicated, stakeholder graph built)

---

### Phase 4: Frontend UI (Est. 2-3 days)
**Goal**: Create simple web interface to browse results

- [ ] Create `frontend/` directory with Express app
- [ ] Write `server.js` (express server on port 3000)
- [ ] Create `public/index.html` (single-page layout)
- [ ] Implement `public/app.js` (vanilla JS, fetch from backend)
- [ ] Create tables:
  - Projects (name, confidence, message count, key stakeholders)
  - Stakeholders (name, role, projects, influence)
  - Timeline (engagement pattern, date range)
- [ ] Add Report viewer (render Markdown or display JSON)
- [ ] Add file upload UI (select PST, configure date range)
- [ ] Test data flow (frontend → backend → Ollama)

**Deliverables**:
- Web UI running on localhost:3000
- Browse projects, stakeholders, timeline
- View generated reports

---

### Phase 5: Polish & Testing (Est. 1 day)
**Goal**: Finalize, test end-to-end, document

- [ ] Add progress bar / streaming status updates
- [ ] Add error messaging & handling
- [ ] Add prompt selector in UI (choose variant from `config.json`)
- [ ] Manual validation: review 20-30 results, check accuracy
- [ ] Write `README.md` (setup, usage, troubleshooting)
- [ ] Test full flow: PST → parse → enrich → reports → UI
- [ ] Adjust confidence thresholds if needed
- [ ] Document any prompt refinements

**Deliverables**:
- End-to-end MVP working
- Documentation complete
- Ready for iteration

---

## Success Criteria

**Phase 1**:
- PST parses without errors
- ≥90% message capture rate
- SQLite schema correct (messages, conversations, attachments)

**Phase 2**:
- Ollama integration stable (<10% retry rate)
- All 4 LLM tasks producing JSON output
- Confidence scores in reasonable range (0.3-0.95)

**Phase 3**:
- Full enrichment completes without crashing
- 10+ unique projects identified
- Stakeholder graph has key team members
- Reports generate in all formats (JSON, Markdown, CSV)

**Phase 4**:
- Web UI displays data correctly
- Fetch API calls work (frontend → backend → Ollama)
- No console errors in browser

**Phase 5**:
- Manual review validates ≥70% accuracy on medium-confidence items
- All features working end-to-end
- Documentation clear enough for future iteration

---

## Key Constraints & Decisions

1. **Date Range**: Configurable in `config.json` (currently Q4 2025: 2025-10-01 to 2025-12-31)
2. **Conversation Threshold**: Only threads with 3+ messages (filter noise)
3. **Ollama Access**: Localhost:11434 (SSH tunnel established externally)
4. **Explainability**: All extractions include reasoning chains + confidence scores (no black box)
5. **Fault Tolerance**: Handle API timeouts gracefully (3 retries, exponential backoff)
6. **No build step**: Frontend uses Tailwind CDN, vanilla JS (no webpack, no npm build)

---

## API Endpoints (FastAPI Backend)

### `POST /parse`
Upload PST file, start parsing job
```json
{
  "file": "<PST file>",
  "date_range": {
    "start": "2025-10-01",
    "end": "2025-12-31"
  }
}
```
Response: `{ "job_id": "abc123", "status": "parsing" }`

### `GET /status?job_id=abc123`
Check parsing/enrichment progress
```json
{
  "job_id": "abc123",
  "status": "enriching",
  "progress": "150/350 messages",
  "current_task": "task_a_projects"
}
```

### `GET /results?job_id=abc123`
Get enriched data
```json
{
  "messages": [...],
  "projects": [...],
  "stakeholders": [...],
  "temporal_pattern": {...}
}
```

### `GET /report?format=markdown`
Get generated report (markdown, json, or csv)

### `GET /config`
Get current config (prompts, thresholds)

### `POST /config`
Update config (select different prompt variants)

---

## Development Workflow

### Server Setup (One-time)

```bash
# On Linux server:
ssh user@server
cd /opt
git clone https://github.com/yourusername/sift.git
cd sift/backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
mkdir -p data/outputs
python main.py  # Keep running
```

### Local Development Setup

**Terminal 1: SSH Tunnel (keep running)**
```bash
# On Windows laptop:
ssh -L 5000:localhost:5000 -L 11434:localhost:11434 user@server
# This creates tunnels: localhost:5000 → server:5000, localhost:11434 → server:11434
```

**Terminal 2: Start Frontend**
```bash
cd frontend
npm install  # First time only
npm start  # Runs on localhost:3000
```

**Terminal 3: Monitor Backend (optional)**
```bash
ssh user@server
tail -f /opt/sift/backend/logs/*.log
```

### Testing Phase 1 (PST Parsing)

With tunnels running, test backend locally:

```bash
# Test health check:
curl http://localhost:5000/

# Test parsing (upload PST):
curl -X POST http://localhost:5000/parse \
  -F "file=@data/sample.pst" \
  -F "date_start=2025-10-01" \
  -F "date_end=2025-12-31"

# Check status:
curl http://localhost:5000/status/job123

# View database on server:
ssh user@server
sqlite3 /opt/sift/data/messages.db "SELECT COUNT(*) FROM messages;"
```

**Day 3-4: Phase 2 (Ollama Integration)**
```bash
# Ensure SSH tunnel: ssh -L 11434:localhost:11434 user@remote
# Test Ollama:
curl http://localhost:11434/api/tags

# Run enrichment:
curl http://localhost:5000/results?job_id=xyz
# Check logs:
tail -f backend/logs/enrichment.log
```

**Day 5: Phase 3 (Aggregation)**
```bash
# Full run:
curl http://localhost:5000/parse -F "file=@data/full.pst"
# Monitor:
watch curl http://localhost:5000/status?job_id=xyz
# Inspect reports:
cat data/outputs/Q4_2025_Summary.md
```

**Day 6-7: Phase 4 (Frontend)**
```bash
cd frontend
npm install
npm start  # Express on :3000

# Open browser:
# http://localhost:3000
```

**Day 8: Phase 5 (Polish)**
- Manual QA testing
- Adjust prompts if needed
- Finalize documentation

---

## Iteration Strategy

After Phase 5, core workflow is:
1. Adjust prompts in `prompts/*.json`
2. Re-run enrichment (backend doesn't need rebuild)
3. Review results in web UI
4. Repeat until satisfaction

No code changes needed for prompt experimentation.

---

## Next Steps

1. ✅ Tech stack decided
2. ✅ Plan documented (this file)
3. ⏭️ **Phase 1: Backend Foundation**
   - Create `backend/` scaffolding
   - Implement PST parser
   - Set up FastAPI + SQLite

Ready to proceed with Phase 1? 🚀
