# Sift - Email Intelligence Extraction

A hybrid Python + Node.js application that extracts work intelligence from PST email archives using local Ollama LLM.

## Quick Start

### Prerequisites

- **Windows Laptop**: Node.js 16+, SSH client
- **Linux Server**: Python 3.9+, Ollama, Git
- SSH access to Linux server

### Architecture

```
Windows Laptop (Dev)          Linux Server
┌─────────────────┐           ┌──────────────┐
│ Frontend        │           │ Backend      │
│ localhost:3000  │──tunnel───│ :5000        │
│ (Express)       │ SSH       │ (FastAPI)    │
│                 │           │              │
│ localhost:5000  │◄──────────│ :5000        │
│ (tunneled)      │           │ (data)       │
└─────────────────┘           │              │
                              │ Ollama       │
                              │ localhost    │
                              │ :11434       │
                              └──────────────┘
```

### Setup

#### 1. Server Setup (One-time)

Follow **DEPLOYMENT.md** for server setup:
- Clone repo on server
- Install Python dependencies
- Start backend on server

#### 2. Local Setup

```bash
cd frontend
npm install
```

### Running

#### Terminal 1: SSH Tunnel

```bash
ssh -L 5000:localhost:5000 -L 11434:localhost:11434 user@server
```

Keep this running. This creates tunnels:
- `localhost:5000` → `server:5000` (backend)
- `localhost:11434` → `server:11434` (Ollama)

#### Terminal 2: Start Frontend

```bash
cd frontend
npm start
```

Frontend runs on `http://localhost:3000`

#### Terminal 3: Monitor Backend (optional)

```bash
ssh user@server
tail -f /opt/sift/backend/logs/*.log
```

Backend automatically accessible at `http://localhost:5000` (via tunnel)

### Workflow

1. **Upload PST** (via web UI at `http://localhost:3000`)
   - Select .pst file
   - Choose date range (default: Q4 2025)
   - Click "Parse"

2. **Monitor Progress** (web UI updates live)
   - See: Messages extracted, Conversations identified

3. **View Results**
   - Projects extracted with confidence scores
   - Stakeholders identified with roles
   - Timeline of engagement
   - Downloadable reports (JSON, Markdown, CSV)

---

## Project Structure

```
sift/
├── plan.md                     # Development plan & phases
├── CLAUDE.md                   # Project overview
├── config.json                 # Configuration (prompts, thresholds)
├── prompts/                    # LLM prompt templates
│   ├── task_a_projects_v1.json
│   ├── task_b_stakeholders_v1.json
│   ├── task_c_importance_v1.json
│   └── task_d_meetings_v1.json
├── backend/                    # Python FastAPI backend
│   ├── main.py                 # Entry point
│   ├── requirements.txt
│   ├── app/
│   │   ├── models.py           # SQLAlchemy ORM
│   │   ├── pst_parser.py       # PST extraction
│   │   ├── ollama_client.py    # Ollama API (Phase 2)
│   │   ├── enrichment.py       # LLM tasks (Phase 2)
│   │   ├── aggregator.py       # Clustering (Phase 3)
│   │   ├── reporter.py         # Report generation (Phase 3)
│   │   └── utils.py            # Logging & helpers
│   └── logs/                   # Processing logs
├── frontend/                   # Node.js Express + vanilla JS
│   ├── server.js
│   ├── package.json
│   └── public/
│       ├── index.html
│       ├── styles.css
│       └── app.js
├── data/                       # Runtime data
│   ├── messages.db             # SQLite database
│   └── outputs/
│       ├── enriched.json
│       ├── Q4_2025_Summary.md
│       └── projects_summary.csv
└── docs/                       # Technical documentation
    └── ollama_api_reference.md
```

---

## API Endpoints

### Backend (FastAPI)

- `GET /` - Health check
- `POST /parse` - Upload PST file and start parsing
- `GET /status/{job_id}` - Check parsing/enrichment progress
- `GET /results/{job_id}` - Get enriched results (Phase 2+)

See `http://localhost:5000/docs` for interactive API docs.

---

## Configuration

Edit `config.json` to customize:

- **Ollama**: URL, model, timeout, retries
- **Prompts**: Select which prompt variant to use
- **Processing**: Batch size, date range, message threshold
- **Output**: Formats (JSON, Markdown, CSV)

### Switching Prompts

To test a different prompt strategy:

1. Edit `config.json`:
   ```json
   "prompts": {
     "task_a_projects": "task_a_projects_v2_aggressive"
   }
   ```

2. Re-run enrichment (backend will reload new prompt)

---

## Development Phases

### Phase 1: Backend Foundation ✅
- [x] PST parsing
- [x] SQLite database
- [x] FastAPI scaffolding
- [ ] **Testing**: Verify with sample PST

### Phase 2: Ollama Integration (WIP)
- [ ] Connect to Ollama API
- [ ] Implement 4 LLM tasks
- [ ] Batch processing
- [ ] Error handling

### Phase 3: Aggregation & Reports
- [ ] Project clustering
- [ ] Stakeholder deduplication
- [ ] Report generation

### Phase 4: Web UI
- [ ] Express server
- [ ] File upload
- [ ] Results display

### Phase 5: Polish
- [ ] Progress tracking
- [ ] Prompt selector UI
- [ ] Error messaging

---

## Logs

Processing logs are written to: `backend/logs/enrichment.log`

Check logs to monitor:
- PST parsing progress
- Ollama API performance
- Extraction confidence scores
- Errors and warnings

```bash
tail -f backend/logs/enrichment.log
```

---

## Troubleshooting

### "Ollama not accessible" error

Ensure SSH tunnel is running:
```bash
ssh -L 11434:localhost:11434 user@remote
```

Test Ollama:
```bash
curl http://localhost:11434/api/tags
```

### "pypff: No such module" error

Install PST parsing library:
```bash
pip install pypff
```

### "SQLite database locked" error

Close other connections to `data/messages.db` and retry.

---

## Performance Expectations

| Phase | Duration | Bottleneck |
|-------|----------|-----------|
| PST Parse (300 msgs) | 30-60s | Disk I/O |
| Enrichment (300 msgs, 4 tasks) | 15-30 min | Ollama inference |
| Aggregation | 1-2 min | Clustering |
| Report Generation | <1 min | I/O |
| **Total** | **20-35 min** | Ollama (7B model) |

---

## Next Steps

1. Test Phase 1 with sample PST file
2. Integrate Ollama API (Phase 2)
3. Test prompts in OpenWebUI: `http://localhost:3000`
4. Build frontend (Phase 4)
5. Manual validation of results

---

## Support

For issues, check:
1. `backend/logs/enrichment.log` for errors
2. FastAPI docs: `http://localhost:5000/docs`
3. Console output (frontend & backend)

---

## License

Internal project. Do not distribute.
