"""
Test enrichment pipeline

Run enrichment on sample messages and verify extraction results

Usage:
    python test_enrich.py              # Test with default settings
    python test_enrich.py 10           # Test with max 10 messages
    python test_enrich.py 10 granite   # Test with specific model
"""
import requests
import json
import time
import sys

BASE_URL = 'http://localhost:5000'

def test_ollama_ready():
    """Verify Ollama and model are ready"""
    print("\n" + "="*80)
    print(" 1. CHECKING OLLAMA & MODEL")
    print("="*80)

    try:
        r = requests.get(f'{BASE_URL}/models')
        if r.status_code != 200:
            print(f"❌ Cannot access models: {r.status_code}")
            print(f"   {r.json()}")
            return False

        data = r.json()
        current_model = data.get('current_model')
        models = data.get('available_models', [])

        if not current_model:
            print("❌ No model selected!")
            if models:
                print("\nAvailable models:")
                for m in models:
                    print(f"  • {m['name']} ({m['size_gb']} GB)")
                print("\nSelect one with: curl -X POST http://localhost:5000/models/{model_name}")
            return False

        print(f"✅ Current model: {current_model}")
        print(f"✅ Available models: {len(models)}")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_pending_messages():
    """Check how many messages are ready for enrichment"""
    print("\n" + "="*80)
    print(" 2. CHECKING PENDING MESSAGES")
    print("="*80)

    try:
        r = requests.get(f'{BASE_URL}/stats')
        if r.status_code != 200:
            print(f"❌ Cannot get stats: {r.status_code}")
            return False

        data = r.json()
        db_stats = data.get('database', {})
        pending = db_stats.get('pending_enrichment', 0)
        total = db_stats.get('messages', 0)

        print(f"✅ Total messages: {total}")
        print(f"✅ Pending enrichment: {pending}")

        if pending == 0:
            print("\n❌ No messages ready for enrichment!")
            print("   Parse a PST file first using /parse endpoint")
            return False

        return pending > 0

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_start_enrichment(max_messages=None):
    """Start enrichment job"""
    print("\n" + "="*80)
    print(" 3. STARTING ENRICHMENT JOB")
    print("="*80)

    try:
        payload = {
            "batch_size": 2
        }
        if max_messages:
            payload["max_messages"] = max_messages

        print(f"Payload: {json.dumps(payload, indent=2)}")

        r = requests.post(f'{BASE_URL}/enrich', json=payload)

        if r.status_code != 200:
            print(f"❌ Failed to start enrichment: {r.status_code}")
            print(f"   {r.json()}")
            return None

        data = r.json()
        job_id = data.get('job_id')
        message = data.get('message', '')

        print(f"\n✅ Job created!")
        print(f"   Job ID: {job_id}")
        print(f"   Message: {message}")

        return job_id

    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def monitor_enrichment(job_id, timeout_seconds=300, check_interval=2):
    """Monitor enrichment progress"""
    print("\n" + "="*80)
    print(f" 4. MONITORING ENRICHMENT (Timeout: {timeout_seconds}s)")
    print("="*80)

    start_time = time.time()
    last_count = 0

    while time.time() - start_time < timeout_seconds:
        try:
            r = requests.get(f'{BASE_URL}/enrich/{job_id}/status')
            if r.status_code != 200:
                print(f"❌ Error getting status: {r.status_code}")
                return None

            status = r.json()
            elapsed = int(time.time() - start_time)
            progress = status['progress_percent']
            processed = status['processed_messages']
            total = status['total_messages']
            current_status = status['status']

            # Only print if progress changed
            if processed != last_count:
                print(f"[{elapsed}s] {progress:.1f}% - {processed}/{total} messages | Status: {current_status}")
                last_count = processed

            if current_status == 'completed':
                print(f"\n✅ COMPLETE: Enriched {processed} messages in {elapsed}s")
                return status

            if current_status == 'failed':
                error = status.get('error', 'Unknown error')
                print(f"\n❌ FAILED: {error}")
                return None

            time.sleep(check_interval)

        except Exception as e:
            print(f"❌ Error: {e}")
            return None

    print(f"\n⏱️ TIMEOUT: Job still running after {timeout_seconds}s")
    print("Enrichment may take longer for large batches. Check status periodically.")
    return None


def get_enrichment_results(job_id):
    """Get enrichment results"""
    print("\n" + "="*80)
    print(" 5. ENRICHMENT RESULTS")
    print("="*80)

    try:
        r = requests.get(f'{BASE_URL}/stats')
        if r.status_code != 200:
            print(f"❌ Cannot get stats: {r.status_code}")
            return False

        data = r.json()
        db_stats = data.get('database', {})

        print(f"Messages in database: {db_stats.get('messages', 0)}")
        print(f"Pending enrichment: {db_stats.get('pending_enrichment', 0)}")

        # Sample enriched messages (this would need a new endpoint for actual extraction results)
        print("\nNote: Check database directly for extraction results:")
        print("  SELECT * FROM extractions LIMIT 5;")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    print("\n" + "="*80)
    print(" SIFT ENRICHMENT PIPELINE TEST")
    print("="*80)
    print("\nPrerequisites:")
    print("  1. SSH tunnel active: ssh -L 5000:localhost:5000 user@server")
    print("  2. Backend running: python main.py")
    print("  3. Ollama running with a model selected")
    print("  4. PST file parsed (messages in database)")
    print("="*80)

    # Parse arguments
    max_messages = None
    model_to_select = None

    if len(sys.argv) > 1:
        try:
            max_messages = int(sys.argv[1])
        except ValueError:
            model_to_select = sys.argv[1]

    if len(sys.argv) > 2:
        model_to_select = sys.argv[2]

    if model_to_select:
        print(f"\nAttempting to select model: {model_to_select}")
        try:
            r = requests.post(f'{BASE_URL}/models/{model_to_select}')
            if r.status_code == 200:
                print(f"✅ Model selected: {model_to_select}")
            else:
                print(f"⚠️  Could not select model: {r.json()}")
        except Exception as e:
            print(f"⚠️  Error selecting model: {e}")

    # Run tests
    if not test_ollama_ready():
        print("\n❌ Ollama not ready. Exiting.")
        return False

    if not test_pending_messages():
        print("\n❌ No pending messages. Exiting.")
        return False

    job_id = test_start_enrichment(max_messages)
    if not job_id:
        print("\n❌ Could not start enrichment. Exiting.")
        return False

    result = monitor_enrichment(job_id)
    if not result:
        print("\n⚠️  Enrichment monitoring ended")
    else:
        get_enrichment_results(job_id)

    print("\n" + "="*80)
    print(" TEST COMPLETE")
    print("="*80 + "\n")

    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
