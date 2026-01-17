# RAG Query Interface - User Guide

## Overview

The **Retrieval-Augmented Generation (RAG)** feature enables you to ask natural language questions about your enriched email data. After enrichment, instead of just aggregating and reporting, you can now have a conversational interface to explore your email intelligence.

**Example queries:**
- "What projects involved cloud migration?"
- "Who were the key stakeholders in Q4?"
- "Summarize all CRITICAL initiatives"
- "What did we decide about infrastructure?"

---

## Architecture

The RAG system uses three main components:

### 1. **Vector Store** (ChromaDB)
- Stores embeddings of all enriched messages
- Uses `nomic-embed-text` model via Ollama
- Enables semantic similarity search

### 2. **RAG Engine** (Backend)
- Retrieves top-10 similar messages based on query
- Builds context from enriched extractions (projects, stakeholders, importance)
- Uses Ollama LLM to synthesize answers with citations
- Maintains chat history for multi-turn conversations

### 3. **Chat Interface** (Frontend)
- User messages (right-aligned, blue bubbles)
- Assistant responses (left-aligned, gray bubbles)
- Citation cards (clickable to expand full message)
- Full message modal (shows email body + extracted data)

---

## User Workflow

### Step 1: Prepare Data
```
1. Upload PST file
2. Parse emails (date range, conversation threshold)
3. Run Enrichment (extracts projects, stakeholders, importance, meetings)
4. View Pipeline page
```

### Step 2: Generate Embeddings
After enrichment completes, the **RAG card** appears on the Pipeline page:

```
RAG Query Card:
├─ Status: "Ready"
├─ Info: "Click 'Generate Embeddings' to enable semantic search"
└─ Button: "Generate Embeddings"
```

**Click "Generate Embeddings":**
- Background job starts indexing all enriched messages
- Progress bar updates in real-time
- Typically takes 1-2 minutes for 1,000 messages
- When complete: "Start RAG Session" button appears

### Step 3: Start Chat Session
Click **"Start RAG Session"** to open the chat interface:

```
Chat Page:
├─ Welcome message with example queries
├─ Message history area (scrollable)
├─ Textarea input ("Ask a question...")
└─ Send button
```

### Step 4: Ask Questions
Type your question and press:
- **Enter** → Send query
- **Shift+Enter** → Add newline

**What happens:**
1. Query gets embedded (semantic vector)
2. System searches for top-10 similar messages in vector store
3. Extractions (projects, stakeholders, etc.) loaded from database
4. LLM synthesizes answer with context from retrieved messages
5. Response displayed with citation cards

### Step 5: View Sources
Each LLM response includes **citation cards**:
```
📎 Sources (N emails):
[1] Subject Line
    From: sender@company.com • Date
```

**Click a citation card** to open full message modal:
```
Modal shows:
├─ Full email subject, sender, date, to, body
├─ Raw email text (pre-formatted)
└─ Extracted Intelligence:
   ├─ Projects identified
   ├─ Stakeholders with roles
   ├─ Importance tier
   └─ Meeting information (if applicable)
```

### Step 6: Follow-up Questions
Each question adds to chat history:
- System includes previous 5 turns in LLM context
- Follow-ups can reference earlier answers
- Multi-turn reasoning supported
- Chat history stored in database (viewable via API)

---

## Technical Details

### Vector Store Setup

**Requirements:**
- ChromaDB 0.4.22+ (added to requirements.txt)
- Ollama running locally with `nomic-embed-text` model
- Connection to Ollama API (default: http://localhost:11434)

**First-time setup:**
```bash
# Pull embedding model if not already available
ollama pull nomic-embed-text

# ChromaDB will auto-create persistent storage at ./data/chroma/
```

### Embedding Generation

The **embedding job**:
- Runs in background (non-blocking)
- Indexes each enriched message with:
  - Email subject + body
  - Extracted projects, stakeholders, importance tier
  - Message metadata (sender, date, importance_tier)
- Creates embeddings using nomic-embed-text (768 dimensions)
- Stores in ChromaDB with metadata for filtering
- Updates `message_embeddings` table with tracking info

**Performance:**
- ~100-200 messages/minute (RTX 5070 + nomic-embed-text)
- 1,000 messages ≈ 5-10 minutes
- Can regenerate anytime (clears and re-indexes all)

### Query Processing

**Flow:**
```
User Query
  ↓
[1] Generate embedding of query (nomic-embed-text)
  ↓
[2] ChromaDB similarity search (top-10 messages)
  ↓
[3] Load full message details + extractions from SQLite
  ↓
[4] Build context prompt:
    - System message (role definition)
    - Chat history (last 5 turns)
    - Retrieved messages with formatting
  ↓
[5] Call Ollama LLM (configured model)
  ↓
[6] Parse response + citations
  ↓
LLM Answer with Source References
```

**Token Management:**
- Retrieves up to 10 messages per query
- Truncates if context exceeds ~4000 tokens (estimate)
- Prioritizes recent messages in context
- Full email bodies stored in database for modal expansion

### Chat History Persistence

**Session Storage:**
```
Database Tables:
├─ rag_sessions
│  ├─ id (UUID)
│  ├─ created_at
│  ├─ last_query_at
│  └─ query_count
│
└─ rag_query_history
   ├─ id (auto-increment)
   ├─ session_id (FK to rag_sessions)
   ├─ query (user input)
   ├─ answer (LLM response)
   ├─ citations_json (message references)
   ├─ retrieved_count
   └─ created_at
```

**Session Behavior:**
- Sessions are ephemeral in browser UI (cleared on page reload)
- Full history persistent in database (for debugging/audit)
- Each new session gets unique ID
- Can retrieve history via `/rag/{session_id}/history` API

### API Endpoints

#### Create Session
```http
POST /rag/session
Response: { "session_id": "a1b2c3d4" }
```

#### Submit Query
```http
POST /rag/{session_id}/query
Content-Type: application/json

{
  "query": "What projects involved cloud migration?",
  "chat_history": [
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "..."}
  ]
}

Response: {
  "answer": "Based on the emails, several projects involved cloud migration:\n\n1. Project Alpha - AWS migration...",
  "citations": [
    {
      "message_id": 123,
      "subject": "Q4 Infrastructure Planning",
      "date": "2025-10-15T10:30:00",
      "sender": "alice@company.com",
      "snippet": "We need to migrate all services to AWS by..."
    },
    ...
  ],
  "retrieved_count": 7
}
```

#### Generate Embeddings
```http
POST /rag/embeddings/generate
Response: { "job_id": "job-abc123", "status": "queued" }

# Poll status
GET /rag/embeddings/status/job-abc123
Response: {
  "job_id": "job-abc123",
  "status": "processing",
  "progress_percent": 45,
  "message": "Embedding generation 45% complete"
}
```

#### Get Message Details
```http
GET /rag/message/{message_id}
Response: {
  "message_id": 123,
  "subject": "Q4 Infrastructure Planning",
  "sender": {
    "name": "Alice Chen",
    "email": "alice@company.com"
  },
  "date": "2025-10-15T10:30:00",
  "body": "We need to migrate all services to AWS...",
  "extractions": {
    "task_a_projects": {...},
    "task_b_stakeholders": {...},
    "task_c_importance": {...},
    "task_d_meetings": {...}
  }
}
```

---

## Troubleshooting

### Issue: "Embedding model not available"
**Solution:** Pull the embedding model:
```bash
ollama pull nomic-embed-text
```

### Issue: "ChromaDB not found" or "pip install chromadb"
**Solution:** Install dependencies:
```bash
pip install -r backend/requirements.txt
```

### Issue: Embedding generation stuck or slow
**Possible causes:**
- Ollama not running (check http://localhost:11434)
- Large number of messages (>5,000)
- Slow GPU or CPU
- Network timeout to Ollama

**Solutions:**
- Verify Ollama is running: `ollama serve`
- Check Ollama logs for errors
- Increase timeout in `backend/main.py` (currently 30s)

### Issue: "No enriched messages found"
**Solution:** Must complete enrichment before generating embeddings
1. Go to Pipeline page
2. Verify "Enrichment" card shows "Completed" status
3. Then generate embeddings

### Issue: Chat returns generic answer instead of specific info
**Possible causes:**
- Query too vague or doesn't match email content
- LLM model not trained on similar topics
- Retrieved messages don't contain relevant information

**Solutions:**
- Ask more specific questions ("Project X status" vs "Projects")
- Check that enrichment completed successfully
- Verify retrieved messages contain expected data (click citations)

---

## Performance Tuning

### Embedding Generation Speed
```python
# backend/app/rag_engine.py

# Adjust batch size for embedding (currently processes 1 at a time)
# Could implement batching: embed 10 messages at once

# Adjust context window
max_context_tokens = 4000  # Increase for longer context

# Adjust retrieval count
top_k = 10  # Increase to retrieve more messages
```

### Query Response Speed

**Factors:**
1. **Vector search** (ChromaDB): ~50-100ms for 10K messages
2. **Database queries** (SQLite): ~10-50ms per message fetch
3. **LLM inference** (Ollama): ~2-5 seconds (depends on model size)

**Optimization:**
- Use faster LLM model (Mistral instead of Granite)
- Reduce `top_k` from 10 to 5
- Add caching for frequent queries
- Pre-compute embeddings (already done at generation time)

---

## Advanced Usage

### Filtering by Importance
The RAG system currently uses all messages. Future enhancement:
```python
# In rag_engine.py: Could filter by importance tier
search_results = self.vector_store.search(
    query,
    top_k=top_k,
    where_filter={"importance_tier": "CRITICAL"}
)
```

### Date Range Queries
Could add date filtering:
```python
# "Emails from Q4" would filter by delivery_date
where_filter={
    "date": {"$gte": "2025-10-01", "$lte": "2025-12-31"}
}
```

### Multi-turn Reasoning
Already supported! Follow-up questions automatically include chat history in LLM context:
- Q: "What's the status of Project X?"
- A: "Project X is in execution phase with..."
- Q: "Who's the project manager?" (refers to Project X from context)
- A: "Based on the emails about Project X, Alice Chen is the PM..."

---

## Future Enhancements

1. **Hybrid Search** - Combine semantic + keyword (BM25) search
2. **Query Suggestions** - "People also asked..." based on data patterns
3. **Export Transcripts** - Download chat as Markdown with citations
4. **Persistent Sessions** - Resume chats from browser history
5. **Advanced Filters** - UI controls for date range, importance, people
6. **Configurable Prompts** - Edit RAG system prompt in prompts/ folder
7. **Streaming Responses** - Stream LLM output as it generates
8. **Multi-user Support** - Auth system for shared analysis

---

## Architecture Diagram

```
Frontend (Chat UI)
      ↓
POST /rag/{session_id}/query
      ↓
Backend (main.py)
      ↓
RAGEngine
├─ VectorStore.search()           → ChromaDB
├─ Load Messages + Extractions    → SQLite
├─ Build Context Prompt
└─ ollama_client.chat()           → Ollama API
      ↓
Response (answer + citations)
      ↓
Frontend (Display + Citation Modal)
```

---

## Database Schema

```sql
-- RAG Session Management
CREATE TABLE rag_sessions (
    id VARCHAR(100) PRIMARY KEY,
    created_at DATETIME DEFAULT NOW(),
    last_query_at DATETIME,
    query_count INTEGER DEFAULT 0
);

-- Query History
CREATE TABLE rag_query_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id VARCHAR(100) FOREIGN KEY REFERENCES rag_sessions(id),
    query TEXT NOT NULL,
    answer TEXT NOT NULL,
    citations_json TEXT,
    retrieved_count INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT NOW()
);

-- Embedding Metadata
CREATE TABLE message_embeddings (
    message_id INTEGER PRIMARY KEY FOREIGN KEY REFERENCES messages(id),
    embedding_generated_at DATETIME DEFAULT NOW(),
    embedding_model VARCHAR(100) DEFAULT 'nomic-embed-text',
    indexed_in_chroma BOOLEAN DEFAULT TRUE
);

-- Embeddings stored separately in ChromaDB
-- ChromaDB: ./data/chroma/
-- Collection: "messages" (768-dimensional embeddings)
```

---

## Support & Debugging

For issues:
1. Check backend logs: `backend/logs/`
2. Verify Ollama running: `curl http://localhost:11434/api/tags`
3. Check database: `sqlite3 data/messages.db` → `.tables`
4. Enable debug logging in `backend/main.py`: `logger.setLevel(logging.DEBUG)`

For feature requests or bugs, refer to the main Sift documentation.
