# Ubuntu Linux Deployment Guide

## System Requirements

### Build Dependencies

Before installing Python dependencies, ensure your Ubuntu system has the required build tools for compiling libratom's native C++ extensions:

```bash
sudo apt update
sudo apt install -y \
  build-essential \
  python3-dev \
  python3-pip \
  python3-venv \
  git \
  autoconf \
  automake \
  libtool \
  pkg-config
```

**Why these are needed:**
- `build-essential` - GCC compiler and build tools
- `python3-dev` - Python headers for C extensions
- `autoconf`, `automake`, `libtool`, `pkg-config` - libpff build dependencies
- libratom internally uses libpff-python-ratom which requires compilation on Linux

### Python Version

- **Python 3.11+** (tested with 3.11.9)
- Virtual environment recommended

## Installation Steps

### 1. Create Virtual Environment

```bash
cd sift/backend
python3.11 -m venv venv
source venv/bin/activate
```

### 2. Upgrade pip

```bash
pip install --upgrade pip setuptools wheel
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

**Expected output during installation:**
```
...
Building wheel for libpff-python-ratom (setup.py) ... done
Successfully installed libratom-0.7.1
Successfully installed chromadb-0.4.15
Successfully installed pydantic-1.10.2
...
```

### 4. Verify Installation

```bash
python << 'EOF'
# Test PST parsing
from libratom.lib.pff import PffArchive
print("✓ libratom imports successfully")

# Test RAG vector store
import chromadb
client = chromadb.Client()
print(f"✓ ChromaDB {chromadb.__version__} works")

# Test Pydantic version
import pydantic
print(f"✓ Pydantic {pydantic.__version__}")

# Test backend modules
import sys
sys.path.insert(0, '.')
from app.models import RAGSession, Message
from app.vector_store import VectorStore
from app.rag_engine import RAGEngine
from app.pst_parser import PSTParser
print("✓ All backend modules import successfully")
EOF
```

## Running the Backend

### Start the Application

```bash
cd sift/backend
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 5000 --reload
```

### Verify Backend is Running

```bash
curl http://localhost:5000/status
```

Expected response: HTTP 200 with status data

## Feature Overview

### PST Parsing Workflow

1. **Upload PST File**: `POST /upload`
   - User uploads .pst file via web UI
   - File validated and saved to `data/` directory

2. **Parse PST**: `POST /parse`
   - Extracts messages from PST archive
   - Stores to SQLite database
   - Creates background job with progress tracking
   - Filters conversations with 3+ messages only

3. **Check Status**: `GET /status/{job_id}`
   - Monitor parsing progress
   - Polls job status

### RAG Query Workflow

1. **Generate Embeddings**: `POST /rag/embeddings/generate`
   - After enrichment completes
   - Indexes all enriched messages into ChromaDB
   - Creates 768-dimensional vectors using nomic-embed-text via Ollama
   - Background job with progress tracking

2. **Create Chat Session**: `POST /rag/session`
   - Starts new RAG session
   - Returns session_id for conversation

3. **Submit Query**: `POST /rag/{session_id}/query`
   - Natural language question about email data
   - Returns synthesized answer with citations
   - Includes full conversation history for multi-turn context

4. **View Message Details**: `GET /rag/message/{message_id}`
   - Expand citation to see full email
   - Shows extracted data (projects, stakeholders, importance, meetings)

## Ollama Integration

### Prerequisites

Ensure Ollama is running with required models:

```bash
# Start Ollama server (if not already running)
ollama serve

# In another terminal, pull required models:
ollama pull granite-embedding-125m-english  # For RAG embeddings
ollama pull granite:7b                      # Or your preferred LLM for chat responses
```

### Verify Embedding Model

After pulling the model, verify it's available:

```bash
ollama list
# Should show: granite-embedding-125m-english

# Test embedding generation:
curl http://localhost:11434/api/embeddings \
  -d '{"model": "granite-embedding-125m-english", "prompt": "test"}' \
  | jq '.embedding | length'
# Should return embedding dimension (e.g., 384 or 768)
```

### Configuration

The backend connects to Ollama at `http://localhost:11434` by default.

To change the Ollama endpoint, modify `backend/main.py` or set environment variable:
```bash
export OLLAMA_URL=http://your-server:11434
```

## Database

SQLite database location: `backend/data/messages.db`

Automatic initialization on first run:
- Creates tables: messages, conversations, extractions, etc.
- Creates RAG tables: rag_sessions, rag_query_history, message_embeddings
- Creates indexes for performance

## Vector Store (ChromaDB)

Persistent storage location: `backend/data/chroma/`

Automatically created on first embedding generation:
- Stores message embeddings (dimension depends on embedding model)
- Uses DuckDB backend for persistence
- For granite-embedding-125m-english: auto-detects embedding dimensions
- Collection: "messages"
- Can be cleared and regenerated anytime

## Development vs Production

### Development (Windows with mocked data)

```bash
# Can test RAG with mock messages in SQLite
# PST parsing unavailable (no libratom on Windows)
# Focus on: RAG endpoints, enrichment, aggregation, reports
```

### Production (Ubuntu Linux)

```bash
# Full feature set available
# PST parsing: Upload → Parse → Extract to SQLite
# RAG: Embeddings → Chat → Citations
# Reports: Aggregation → JSON/Markdown output
```

## Troubleshooting

### Build Failures

**Error: `Microsoft Visual C++ 14.0 or greater is required`**
- This error appears on Windows (expected)
- Backend is designed for Ubuntu Linux deployment
- Use Ubuntu for production deployment

**Error: `libpff` build fails**
- Ensure build-essential is installed: `sudo apt install build-essential`
- Check Python development files: `sudo apt install python3-dev`
- Try again: `pip install libratom==0.7.1 --no-cache-dir`

### Import Errors

**Error: `ModuleNotFoundError: No module named 'libratom'`**
- Install dependencies: `pip install -r requirements.txt`
- Verify in virtual environment: `which python` should show venv path

**Error: `pydantic.errors.PydanticImportError: field_validator` not found**
- Check Pydantic version: `pip list | grep pydantic`
- Should be: `pydantic 1.10.2`
- If not: `pip install pydantic==1.10.2 --force-reinstall`

### Connection Errors

**Error: `Connection refused` on ChromaDB operations**
- Verify Ollama is running: `curl http://localhost:11434/api/tags`
- Check ChromaDB data directory exists: `ls backend/data/chroma/`
- Restart backend: `pkill -f uvicorn` then restart

**Error: Ollama embeddings timeout**
- Ensure nomic-embed-text model is pulled: `ollama pull nomic-embed-text`
- Check Ollama logs: `tail -f ~/.ollama/logs/`
- Verify model is loaded: `ollama list`

## Performance Tuning

### Embedding Generation

- Speed: ~100-200 messages/minute on RTX 5070
- 1,000 messages: ~5-10 minutes
- Adjust batch size in `backend/app/rag_engine.py` if needed

### Query Response Time

- Vector search: ~50-100ms (10K messages)
- Database queries: ~10-50ms per message
- LLM inference: ~2-5 seconds (depends on model)
- Total: ~3-6 seconds typical

### Database Optimization

Indexes created automatically for:
- Message timestamps
- Conversation IDs
- Enrichment extraction types
- RAG session tracking

## Security Considerations

- SQLite database stores sensitive email content
- ChromaDB stores message embeddings
- Ollama server should not be exposed to untrusted networks
- Consider firewall rules limiting access to port 5000 and 11434

## Monitoring

### Check Service Health

```bash
# Backend health
curl http://localhost:5000/status

# Ollama health
curl http://localhost:11434/api/tags

# Database integrity
sqlite3 data/messages.db ".tables"
```

### View Logs

```bash
# If running with --log-level debug
uvicorn main:app --log-level debug

# Or check application logs directory
ls backend/logs/
tail -f backend/logs/app.log
```

## Deployment Checklist

- [ ] Ubuntu 22.04+ installed
- [ ] Build dependencies installed (`build-essential`, etc.)
- [ ] Python 3.11+ available
- [ ] Ollama server running and models available
- [ ] Virtual environment created
- [ ] Dependencies installed: `pip install -r requirements.txt`
- [ ] All imports verify successfully
- [ ] Backend starts: `uvicorn main:app`
- [ ] Ollama connectivity verified
- [ ] Test PST parsing (if PST file available)
- [ ] Test RAG feature (embeddings, chat, citations)
- [ ] Configure firewall/reverse proxy if needed
- [ ] Monitor production logs

---

## References

- [libratom GitHub](https://github.com/libratom/libratom)
- [ChromaDB Documentation](https://docs.trychroma.com/)
- [Ollama Models](https://ollama.ai/)
- [FastAPI Deployment](https://fastapi.tiangolo.com/deployment/)
