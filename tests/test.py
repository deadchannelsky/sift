"""
Integration tests for Sift Backend
Run this on Windows machine with SSH tunnel active
"""
import requests
import time
import json

BASE_URL = 'http://localhost:5000'

def test_health_check():
    """Test health check endpoint"""
    print("\n=== 1. Health Check ===")
    try:
        r = requests.get(f'{BASE_URL}/')
        print(f"Status: {r.status_code}")
        print(f"Response: {json.dumps(r.json(), indent=2)}")
        assert r.status_code == 200
        assert r.json()['status'] == 'running'
        print("✅ PASS: Health check working")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_parse_pst(pst_filename='test.pst', date_start='2025-10-01', date_end='2025-12-31'):
    """Test PST parsing endpoint"""
    print("\n=== 2. Parse PST File ===")
    try:
        payload = {
            'pst_filename': pst_filename,
            'date_start': date_start,
            'date_end': date_end,
            'min_conversation_messages': 3
        }
        print(f"Sending: {json.dumps(payload, indent=2)}")

        r = requests.post(f'{BASE_URL}/parse', json=payload)
        print(f"Status: {r.status_code}")
        response = r.json()
        print(f"Response: {json.dumps(response, indent=2)}")

        if r.status_code == 422:
            print("⚠️  Backend expecting old format. Did you pull latest changes and restart?")
            print("   Run on server: cd /opt/sift && git pull origin main && cd backend && python main.py")
            return None

        assert r.status_code == 200, f"Expected 200, got {r.status_code}"
        assert 'job_id' in response
        assert response['status'] == 'queued'

        job_id = response['job_id']
        print(f"✅ PASS: Job created with ID: {job_id}")
        return job_id
    except AssertionError as e:
        print(f"❌ FAIL: {e}")
        return None
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return None


def test_status(job_id):
    """Test status endpoint"""
    print(f"\n=== 3. Check Job Status (ID: {job_id}) ===")
    if not job_id:
        print("❌ SKIP: No job_id from parse test")
        return False

    try:
        r = requests.get(f'{BASE_URL}/status/{job_id}')
        print(f"Status: {r.status_code}")
        response = r.json()
        print(f"Response: {json.dumps(response, indent=2)}")

        assert r.status_code == 200
        assert response['job_id'] == job_id
        print(f"✅ PASS: Status check working (Current: {response['status']})")
        return True
    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def test_monitor_progress(job_id, timeout_seconds=300, check_interval=5):
    """Monitor parsing progress until complete"""
    print(f"\n=== 4. Monitor Progress (Timeout: {timeout_seconds}s) ===")
    if not job_id:
        print("❌ SKIP: No job_id")
        return None

    try:
        start_time = time.time()
        last_count = 0

        while time.time() - start_time < timeout_seconds:
            r = requests.get(f'{BASE_URL}/status/{job_id}')
            status = r.json()

            elapsed = int(time.time() - start_time)
            progress = status['progress_percent']
            processed = status['processed_messages']
            total = status['total_messages']
            current_task = status['current_task']

            # Only print if progress changed
            if processed != last_count:
                print(f"[{elapsed}s] {progress:.1f}% - {processed}/{total} messages | Task: {current_task}")
                last_count = processed

            if status['status'] == 'completed':
                print(f"\n✅ COMPLETE: Parsed {processed} messages in {elapsed}s")
                return status

            time.sleep(check_interval)

        print(f"\n⏱️ TIMEOUT: Job still running after {timeout_seconds}s")
        print(f"Last status: {json.dumps(status, indent=2)}")
        return status

    except Exception as e:
        print(f"❌ FAIL: {e}")
        return None


def test_results(job_id):
    """Test results endpoint"""
    print(f"\n=== 5. Get Results (ID: {job_id}) ===")
    if not job_id:
        print("❌ SKIP: No job_id")
        return False

    try:
        r = requests.get(f'{BASE_URL}/results/{job_id}')
        print(f"Status: {r.status_code}")

        if r.status_code == 400:
            print(f"⚠️  Job not complete yet: {r.json()['detail']}")
            return False

        response = r.json()
        print(f"Response: {json.dumps(response, indent=2)}")

        assert r.status_code == 200
        print(f"✅ PASS: Results retrieved")
        print(f"   - Messages: {response['message_count']}")
        print(f"   - Conversations: {response['conversation_count']}")
        return True

    except Exception as e:
        print(f"❌ FAIL: {e}")
        return False


def main():
    """Run all tests"""
    print("=" * 60)
    print("SIFT BACKEND - INTEGRATION TESTS")
    print("=" * 60)
    print(f"\nBackend URL: {BASE_URL}")
    print("Prerequisites:")
    print("  1. SSH tunnel active: ssh -L 5000:localhost:5000 user@server")
    print("  2. Backend running: python main.py")
    print("  3. PST file on server: /opt/sift/data/test.pst")
    print("\n" + "=" * 60)

    # Run tests
    results = {}

    results['health'] = test_health_check()

    if results['health']:
        job_id = test_parse_pst()
        results['parse'] = job_id is not None

        if job_id:
            results['status'] = test_status(job_id)
            results['monitor'] = test_monitor_progress(job_id) is not None
            results['results'] = test_results(job_id)
            job_id_display = job_id
        else:
            results['status'] = False
            results['monitor'] = False
            results['results'] = False
            job_id_display = "N/A"
    else:
        print("\n⚠️  Skipping remaining tests (backend not responding)")
        results['parse'] = False
        results['status'] = False
        results['monitor'] = False
        results['results'] = False
        job_id_display = "N/A"

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    print(f"Health Check:     {'✅ PASS' if results['health'] else '❌ FAIL'}")
    print(f"Parse PST:        {'✅ PASS' if results['parse'] else '❌ FAIL'}")
    print(f"Check Status:     {'✅ PASS' if results['status'] else '❌ FAIL'}")
    print(f"Monitor Progress: {'✅ PASS' if results['monitor'] else '❌ FAIL'}")
    print(f"Get Results:      {'✅ PASS' if results['results'] else '❌ FAIL'}")
    print(f"\nJob ID: {job_id_display}")
    print("=" * 60)

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\nResult: {passed}/{total} tests passed\n")

    return passed == total


if __name__ == '__main__':
    import sys
    success = main()
    sys.exit(0 if success else 1)
