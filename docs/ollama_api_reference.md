# Ollama API Reference

## Connection

- **Base URL** (via SSH tunnel): `http://localhost:11434`
- **Model** (configurable, default): `mistral:7b`
- **Timeout**: 30 seconds
- **Retry logic**: 3 attempts with exponential backoff (500ms, 1s, 2s)

## Health Check Endpoint

```bash
curl http://localhost:11434/api/tags
```

Returns list of available models and their sizes.

---

## Completion Endpoint (Used in this project)

**Endpoint**: `POST http://localhost:11434/api/generate`

**Request Body** (JSON):
```json
{
  "model": "mistral:7b",
  "prompt": "Your prompt text here",
  "stream": false,
  "temperature": 0.3,
  "num_predict": 500
}
```

**Response**:
```json
{
  "response": "Model's text response here",
  "created_at": "2025-01-13T15:23:45.123Z",
  "done": true,
  "context": [2, 3, 4, ...],
  "total_duration": 1234567890,
  "load_duration": 123456,
  "prompt_eval_count": 45,
  "eval_count": 120,
  "eval_duration": 987654321
}
```

---

## Key Parameters

- **temperature**: 0.3 (low = more deterministic, for structured extraction)
- **num_predict**: 500 (token limit for response)
- **stream**: false (we want complete response, not streaming)

---

## Python Client Pattern

```python
import requests
import json
import time

class OllamaClient:
    def __init__(self, base_url="http://localhost:11434", model="mistral:7b"):
        self.base_url = base_url
        self.model = model
        self.timeout = 30
        self.max_retries = 3

    def call(self, prompt, temperature=0.3, num_predict=500):
        """
        Send prompt to Ollama, handle retries.
        Returns: parsed JSON response or fallback dict with low confidence.
        """
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "temperature": temperature,
            "num_predict": num_predict
        }

        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/api/generate",
                    json=payload,
                    timeout=self.timeout
                )
                response.raise_for_status()
                return response.json()["response"]
            except Exception as e:
                wait_time = 0.5 * (2 ** attempt)  # 500ms, 1s, 2s
                if attempt < self.max_retries - 1:
                    time.sleep(wait_time)
                else:
                    raise
```

---

## Batching & API Call Strategy

### Why Batching?
- Sending 1 message per API call = high latency, slow processing
- Batching 5 messages together = amortize latency, faster throughput
- BUT: Batch context can confuse extraction (model might mix messages)

### Recommended Batch Strategy

```python
class BatchProcessor:
    def __init__(self, ollama_client, batch_size=5):
        self.client = ollama_client
        self.batch_size = batch_size

    def process_messages(self, messages, task_name):
        """
        Process messages in batches, keeping context separate.

        Strategy: Send each message *separately* to Ollama, but batch the requests
        (i.e., 5 concurrent API calls instead of waiting for 1 at a time).
        This keeps extraction clean and parallel processing fast.
        """
        results = []
        for i in range(0, len(messages), self.batch_size):
            batch = messages[i:i+self.batch_size]

            # Process batch in parallel
            batch_results = []
            for msg in batch:
                prompt = self._build_prompt(task_name, msg)
                result = self.client.call(prompt)
                batch_results.append((msg.msg_id, result))

            results.extend(batch_results)
            print(f"Processed {len(results)}/{len(messages)} messages...")

        return results
```

---

## Temperature & Determinism

- **Temperature 0.3**: Low randomness, good for factual extraction (projects, people)
- **Temperature 0.5**: Balanced, for reasoning tasks (importance, meeting detection)
- Higher temps = more creative, less suitable for this task

---

## Error Handling & Fallback

### Response Validation

```python
def parse_task_response(raw_response, task_name, msg_id, fallback_confidence=0.2):
    """
    Parse Ollama JSON response. If invalid, return low-confidence fallback.
    """
    try:
        data = json.loads(raw_response)
        # Validate structure (task-specific)
        if task_name == "task_a" and "extractions" not in data:
            raise ValueError("Missing 'extractions' key")
        return data
    except json.JSONDecodeError:
        print(f"[WARN] Failed to parse JSON for {msg_id} (task {task_name})")
        # Return fallback with low confidence
        return {
            "error": "JSON_PARSE_FAILED",
            "raw_response": raw_response[:500],
            "fallback_confidence": fallback_confidence,
            "reasoning": ["Model response was not valid JSON; low confidence in extraction"]
        }
    except Exception as e:
        print(f"[ERROR] {msg_id} (task {task_name}): {str(e)}")
        return {
            "error": str(e),
            "fallback_confidence": 0.0,
            "reasoning": ["Extraction failed; treating as noise"]
        }
```

---

## Logging Strategy

```python
import logging

logger = logging.getLogger("enrichment")
logger.setLevel(logging.INFO)

# File handler
fh = logging.FileHandler("logs/enrichment.log")
fh.setFormatter(logging.Formatter(
    '%(asctime)s - %(levelname)s - %(message)s'
))
logger.addHandler(fh)

# Sample log calls:
logger.info(f"Processing message {msg_id}: {conversation_topic}")
logger.info(f"  Task A (projects): confidence={conf}, extraction='{name}'")
logger.warning(f"  Task B failed to parse JSON, using fallback")
logger.error(f"  Ollama API timeout after 3 retries")
```

---

## Testing Your Prompts (Before Batch Run)

### Manual Test in OpenWebUI

1. Open OpenWebUI at `http://localhost:3000`
2. Select model `mistral:7b`
3. Paste one of the task prompts
4. Use real email data from your PST (copy/paste a body snippet)
5. Refine the prompt until outputs look good
6. Copy the refined prompt back into your code

### Example Test Session

```
[In OpenWebUI]

Prompt:
You are extracting project information from workplace emails...

[Paste Task A prompt + real email data]

Response:
{
  "extractions": [
    {
      "extraction": "Acme MVP",
      "type": "project",
      "confidence": 0.92,
      "signal_strength": "high",
      ...
    }
  ],
  ...
}

✓ Good! Keep this prompt.
```
