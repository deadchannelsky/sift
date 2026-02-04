# REPL Pipeline Implementation Plan

## Overview

This document outlines the implementation of a **REPL (Read-Eval-Print Loop) pipeline** for SIFT, inspired by MIT's RLM (Recursive Language Models) research. The REPL pipeline enables code-based corpus exploration as an alternative to vector-similarity RAG.

## Why REPL for SIFT?

| Aspect | Current RAG Approach | REPL Approach |
|--------|---------------------|---------------|
| **Query type** | Semantic similarity search | Programmatic traversal |
| **Temporal queries** | Poor (similarity ignores time) | Excellent (explicit date filtering) |
| **Aggregation** | Post-hoc clustering | On-demand code-based grouping |
| **Explainability** | "Here are similar messages" | "I ran this code, here's what I found" |
| **Tech demo value** | Standard approach | Novel, shows LLM reasoning process |

## Architecture

### Data Flow

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        REPL Pipeline                                    │
│                                                                         │
│  ┌──────────┐     ┌──────────────┐     ┌──────────────┐                │
│  │  Parse   │────▶│ Corpus Load  │────▶│ REPL Session │                │
│  │  (same)  │     │ (SQLite→Dict)│     │              │                │
│  └──────────┘     └──────────────┘     └──────┬───────┘                │
│                                               │                         │
│                   ┌───────────────────────────▼────────────────────┐   │
│                   │              REPL Loop                         │   │
│                   │                                                │   │
│                   │  User Query: "How did my Platform team         │   │
│                   │              engagement change over Q4?"       │   │
│                   │                      │                         │   │
│                   │                      ▼                         │   │
│                   │  ┌────────────────────────────────────────┐   │   │
│                   │  │ LLM generates Python code:             │   │   │
│                   │  │                                        │   │   │
│                   │  │ platform_msgs = [m for m in corpus     │   │   │
│                   │  │   if 'platform' in m['subject'].lower()│   │   │
│                   │  │   or any('platform' in p.lower()       │   │   │
│                   │  │          for p in m.get('projects',[]))│   │   │
│                   │  │ ]                                      │   │   │
│                   │  │ by_month = group_by_month(platform_msgs│   │   │
│                   │  │ return {m: len(msgs) for m,msgs in ... │   │   │
│                   │  └────────────────────────────────────────┘   │   │
│                   │                      │                         │   │
│                   │                      ▼                         │   │
│                   │  ┌────────────────────────────────────────┐   │   │
│                   │  │ Sandbox executes code:                 │   │   │
│                   │  │ Result: {"Oct": 45, "Nov": 23, "Dec": 8│   │   │
│                   │  └────────────────────────────────────────┘   │   │
│                   │                      │                         │   │
│                   │                      ▼                         │   │
│                   │  ┌────────────────────────────────────────┐   │   │
│                   │  │ LLM interprets result:                 │   │   │
│                   │  │ "Your Platform team engagement dropped │   │   │
│                   │  │ significantly in Q4, from 45 messages  │   │   │
│                   │  │ in October to just 8 in December.      │   │   │
│                   │  │ Let me check what happened..."         │   │   │
│                   │  │                                        │   │   │
│                   │  │ [Optionally generates more code to     │   │   │
│                   │  │  drill into December specifically]     │   │   │
│                   │  └────────────────────────────────────────┘   │   │
│                   │                                                │   │
│                   └────────────────────────────────────────────────┘   │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

### Components

#### 1. Backend: `backend/app/repl_engine.py`

```python
class REPLEngine:
    """
    Executes LLM-generated Python code against email corpus

    Key responsibilities:
    - Load corpus from SQLite into queryable structure
    - Sandbox code execution (safety)
    - Provide helper functions (group_by_month, filter_by_sender, etc.)
    - Track execution trace for visualization
    """

    def __init__(self, db_session, ollama_client, prompt_manager):
        self.corpus = None  # Lazy-loaded
        self.execution_trace = []

    def load_corpus(self) -> List[Dict]:
        """Load all messages + extractions into memory as dicts"""

    def execute_code(self, code: str) -> Tuple[Any, str]:
        """
        Execute Python code in restricted sandbox
        Returns: (result, error_message)
        """

    def query(self, user_question: str, max_iterations: int = 3) -> Dict:
        """
        Main REPL loop:
        1. Generate code from question
        2. Execute code
        3. Interpret result
        4. Optionally iterate (recursive exploration)

        Returns: {
            "answer": str,
            "trace": [
                {"step": 1, "code": "...", "result": "...", "interpretation": "..."},
                ...
            ],
            "corpus_stats": {"total_messages": N, "date_range": "..."}
        }
        """
```

#### 2. Prompt Template: `prompts/repl_code_gen_v1.json`

```json
{
    "prompt_id": "repl_code_gen",
    "version": "1.0.0",
    "description": "Generate Python code to explore email corpus",
    "model_params": {
        "temperature": 0.1,
        "max_tokens": 1000
    },
    "prompt_template": "You are a Python programmer exploring an email corpus...",
    "available_functions": [
        "group_by_month(messages) -> Dict[str, List[Message]]",
        "group_by_sender(messages) -> Dict[str, List[Message]]",
        "filter_by_date_range(messages, start, end) -> List[Message]",
        "count_by_project(messages) -> Dict[str, int]",
        "get_senders(messages) -> Set[str]"
    ]
}
```

#### 3. API Endpoints: `main.py`

```python
# New REPL endpoints
@app.post("/repl/session")
async def create_repl_session():
    """Create new REPL session, load corpus"""

@app.post("/repl/{session_id}/query")
async def repl_query(session_id: str, request: REPLQueryRequest):
    """
    Execute REPL query

    Request: {"question": "...", "max_iterations": 3}
    Response: {
        "answer": "...",
        "trace": [...],  # For visualization
        "session_id": "..."
    }
    """

@app.get("/repl/{session_id}/trace")
async def get_repl_trace(session_id: str):
    """Get full execution trace for visualization"""

@app.get("/repl/corpus/stats")
async def get_corpus_stats():
    """Get corpus statistics (message count, date range, etc.)"""
```

#### 4. Frontend: REPL Page

New page accessible from pipeline dashboard showing:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  SIFT - REPL Explorer                                    [Standard RAG] │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  Corpus: 1,247 messages | Oct 2025 - Dec 2025 | 45 unique senders      │
│                                                                         │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Ask a question about your emails...                            │   │
│  │  ┌─────────────────────────────────────────────────────────┐   │   │
│  │  │ How did my engagement with the Platform team change     │   │   │
│  │  │ over Q4?                                                │   │   │
│  │  └─────────────────────────────────────────────────────────┘   │   │
│  │                                              [Explore]          │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ═══════════════════════════════════════════════════════════════════   │
│                                                                         │
│  EXPLORATION TRACE                                                      │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Step 1: Understanding the question                             │   │
│  │  ─────────────────────────────────────────                      │   │
│  │  > Searching for messages related to "Platform team"            │   │
│  │                                                                 │   │
│  │  Generated Code:                                                │   │
│  │  ┌─────────────────────────────────────────────────────────┐   │   │
│  │  │ platform_msgs = [m for m in corpus                      │   │   │
│  │  │   if 'platform' in m['subject'].lower()                 │ ▼ │   │
│  │  │   or 'platform-team' in m.get('recipients', '').lower()]│   │   │
│  │  │ by_month = group_by_month(platform_msgs)                │   │   │
│  │  │ return {m: len(msgs) for m, msgs in by_month.items()}   │   │   │
│  │  └─────────────────────────────────────────────────────────┘   │   │
│  │                                                                 │   │
│  │  Result:                                                        │   │
│  │  ┌─────────────────────────────────────────────────────────┐   │   │
│  │  │ {"Oct 2025": 45, "Nov 2025": 23, "Dec 2025": 8}         │   │   │
│  │  └─────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Step 2: Investigating the drop                                 │   │
│  │  ─────────────────────────────────────────                      │   │
│  │  > Noticed significant drop in December. Checking details...    │   │
│  │                                                                 │   │
│  │  Generated Code:                                                │   │
│  │  ┌─────────────────────────────────────────────────────────┐   │   │
│  │  │ dec_msgs = filter_by_date_range(platform_msgs,          │   │   │
│  │  │   "2025-12-01", "2025-12-31")                           │ ▼ │   │
│  │  │ return {                                                │   │   │
│  │  │   "count": len(dec_msgs),                               │   │   │
│  │  │   "senders": list(get_senders(dec_msgs)),               │   │   │
│  │  │   "subjects": [m['subject'][:50] for m in dec_msgs[:5]] │   │   │
│  │  │ }                                                       │   │   │
│  │  └─────────────────────────────────────────────────────────┘   │   │
│  │                                                                 │   │
│  │  Result:                                                        │   │
│  │  ┌─────────────────────────────────────────────────────────┐   │   │
│  │  │ {"count": 8, "senders": ["alice@co.com", "bob@co.com"], │   │   │
│  │  │  "subjects": ["Platform EOY wrap-up", "Holiday schedule"│   │   │
│  │  └─────────────────────────────────────────────────────────┘   │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ═══════════════════════════════════════════════════════════════════   │
│                                                                         │
│  ANSWER                                                                 │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │  Your engagement with the Platform team dropped significantly   │   │
│  │  over Q4 2025:                                                  │   │
│  │                                                                 │   │
│  │  • October: 45 messages (peak engagement)                       │   │
│  │  • November: 23 messages (49% decrease)                         │   │
│  │  • December: 8 messages (83% decrease from October)             │   │
│  │                                                                 │   │
│  │  The December messages were primarily from Alice and Bob,       │   │
│  │  focusing on wrap-up and holiday scheduling rather than         │   │
│  │  active project work. This suggests the team may have           │   │
│  │  completed a major milestone or transitioned focus.             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│                                   [Ask Follow-up] [New Question]        │
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

## Implementation Phases

### Phase 1: Backend Core (This PR)
- [ ] `repl_engine.py` - Corpus loader + sandbox executor
- [ ] Prompt template for code generation
- [ ] API endpoints for REPL sessions
- [ ] Basic integration tests

**Manual Test Point**: Can query corpus via API, see code generation + execution

### Phase 2: Frontend UI
- [ ] New REPL page with execution trace visualization
- [ ] Pipeline page shows REPL option after parsing
- [ ] Syntax highlighting for generated code
- [ ] Collapsible trace steps

**Manual Test Point**: Full visual demo of REPL exploration

### Phase 3: Polish
- [ ] Side-by-side comparison with RAG
- [ ] Better error handling for malformed code
- [ ] Iteration limits and safeguards
- [ ] Performance optimization for large corpora

## Safety Considerations

### Code Sandbox

The REPL engine executes LLM-generated Python code. Safety measures:

1. **RestrictedPython** or **subprocess isolation**
   - No file system access
   - No network access
   - No imports beyond whitelist
   - Timeout enforcement

2. **Whitelisted functions only**
   ```python
   ALLOWED_BUILTINS = {
       'len', 'sum', 'min', 'max', 'sorted', 'list', 'dict', 'set',
       'str', 'int', 'float', 'bool', 'range', 'enumerate', 'zip'
   }

   HELPER_FUNCTIONS = {
       'group_by_month', 'group_by_sender', 'filter_by_date_range',
       'count_by_project', 'get_senders', 'get_subjects'
   }
   ```

3. **Output size limits** - Truncate results > 10KB

4. **Iteration limits** - Max 5 REPL loops per query

## Model Requirements

For good code generation, recommend:
- `deepseek-coder:6.7b` or `codellama:7b`
- Current `granite-4.0-h-tiny` may struggle with Python generation

Config option to select REPL-specific model:
```json
{
    "repl": {
        "code_model": "deepseek-coder:6.7b",
        "interpretation_model": "mistral:7b"
    }
}
```

## File Changes Summary

### New Files
- `backend/app/repl_engine.py` - Core REPL logic
- `prompts/repl_code_gen_v1.json` - Code generation prompt
- `prompts/repl_interpret_v1.json` - Result interpretation prompt
- `frontend/repl.html` - REPL explorer page (or section in index.html)

### Modified Files
- `backend/main.py` - New REPL endpoints
- `backend/app/models.py` - REPLSession, REPLTrace models
- `frontend/index.html` - Add REPL navigation
- `frontend/app.js` - REPL page logic
- `frontend/styles.css` - REPL trace styling
- `config.json` - REPL configuration section

## Success Criteria

1. **Functional**: User can ask temporal/aggregation questions and see code-based exploration
2. **Visual**: Execution trace clearly shows what the model is doing
3. **Differentiated**: REPL answers questions that RAG cannot (temporal patterns)
4. **Safe**: No code injection, execution timeouts enforced
5. **Demo-ready**: Team can understand the REPL concept from the UI

## Questions for Review

1. **Model selection**: Should we require a code-capable model for REPL, or try with current model first?
2. **Sandbox approach**: RestrictedPython vs subprocess isolation vs Docker?
3. **Corpus size**: At what message count should we warn about memory usage?
4. **Iteration depth**: How many recursive exploration steps before forcing an answer?

---

*Created: 2026-02-02*
*Status: Planning*
