"""
Test aggregation pipeline

Run project clustering and stakeholder deduplication on enriched messages.
Validates aggregated_projects.json and aggregated_stakeholders.json output.

Usage:
    python test_aggregate.py              # Test aggregation
    python test_aggregate.py --verbose    # Verbose output
"""
import requests
import json
import time
import sys
import os

BASE_URL = 'http://localhost:5000'

def test_backend_ready():
    """Verify backend is running and accessible"""
    print("\n" + "="*80)
    print(" 1. CHECKING BACKEND CONNECTION")
    print("="*80)

    try:
        r = requests.get(f'{BASE_URL}/')
        if r.status_code != 200:
            print(f"❌ Backend not responding: {r.status_code}")
            return False

        data = r.json()
        print(f"✅ Backend: {data.get('name')} v{data.get('version')}")
        print(f"✅ Status: {data.get('status')}")
        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_enriched_messages():
    """Check how many messages have been enriched"""
    print("\n" + "="*80)
    print(" 2. CHECKING ENRICHED MESSAGES")
    print("="*80)

    try:
        r = requests.get(f'{BASE_URL}/stats')
        if r.status_code != 200:
            print(f"❌ Cannot get stats: {r.status_code}")
            return False

        data = r.json()
        db_stats = data.get('database', {})
        total = db_stats.get('messages', 0)
        pending = db_stats.get('pending_enrichment', 0)
        enriched = total - pending

        print(f"✅ Total messages: {total}")
        print(f"✅ Enriched: {enriched}")
        print(f"⏳ Pending enrichment: {pending}")

        if enriched == 0:
            print("\n❌ No enriched messages to aggregate!")
            print("   Run enrichment first using /enrich endpoint")
            return False

        return enriched > 0

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_start_aggregation():
    """Start aggregation job"""
    print("\n" + "="*80)
    print(" 3. STARTING AGGREGATION JOB")
    print("="*80)

    try:
        payload = {
            "output_formats": ["json"]
        }

        print(f"Payload: {json.dumps(payload, indent=2)}")

        r = requests.post(f'{BASE_URL}/aggregate', json=payload)

        if r.status_code != 200:
            print(f"❌ Failed to start aggregation: {r.status_code}")
            error_detail = r.json().get('detail', 'Unknown error')
            print(f"   {error_detail}")
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


def monitor_aggregation(job_id, timeout_seconds=120, check_interval=2):
    """Monitor aggregation progress"""
    print("\n" + "="*80)
    print(f" 4. MONITORING AGGREGATION (Timeout: {timeout_seconds}s)")
    print("="*80)

    start_time = time.time()
    last_count = 0

    while time.time() - start_time < timeout_seconds:
        try:
            r = requests.get(f'{BASE_URL}/aggregate/{job_id}/status')
            if r.status_code != 200:
                print(f"❌ Error getting status: {r.status_code}")
                return None

            status = r.json()
            elapsed = int(time.time() - start_time)
            progress = status['progress_percent']
            processed = status['processed_messages']
            total = status['total_messages']
            current_status = status['status']
            projects = status.get('projects_found', 0)
            stakeholders = status.get('stakeholders_found', 0)

            # Only print if progress changed
            if processed != last_count:
                print(f"[{elapsed}s] {progress:.1f}% - {processed}/{total} messages | Status: {current_status}")
                if current_status == "completed":
                    print(f"   Projects found: {projects}")
                    print(f"   Stakeholders found: {stakeholders}")
                last_count = processed

            if current_status == 'completed':
                print(f"\n✅ COMPLETE: Aggregated {processed} messages in {elapsed}s")
                print(f"   Found {projects} projects and {stakeholders} stakeholders")
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
    print("Aggregation may take longer for large datasets. Check status periodically.")
    return None


def check_output_files():
    """Check if output JSON files were created"""
    print("\n" + "="*80)
    print(" 5. CHECKING OUTPUT FILES")
    print("="*80)

    output_dir = "./data"
    projects_file = os.path.join(output_dir, "aggregated_projects.json")
    stakeholders_file = os.path.join(output_dir, "aggregated_stakeholders.json")

    print(f"\nLooking for output files in: {os.path.abspath(output_dir)}")

    # Check projects file
    if os.path.exists(projects_file):
        print(f"✅ Found: aggregated_projects.json")
        try:
            with open(projects_file, 'r') as f:
                projects_data = json.load(f)
            stats = projects_data.get('stats', {})
            projects = projects_data.get('projects', [])
            print(f"   - Total projects: {stats.get('total_projects', 0)}")
            print(f"   - Total aliases merged: {stats.get('total_aliases_merged', 0)}")
            print(f"   - Avg mentions per project: {stats.get('avg_mentions_per_project', 0)}")
            print(f"   - Processing time: {stats.get('processing_time_ms', 0)}ms")

            if projects and len(projects) > 0:
                print(f"\n   Top 3 projects:")
                for i, proj in enumerate(projects[:3], 1):
                    print(f"     {i}. {proj['canonical_name']}")
                    print(f"        - Mentions: {proj['total_mentions']}")
                    print(f"        - Confidence: {proj['avg_confidence']}")
                    print(f"        - Stakeholders: {len(proj['stakeholders'])}")
        except Exception as e:
            print(f"   ⚠️  Error reading projects file: {e}")
    else:
        print(f"❌ Missing: aggregated_projects.json")

    # Check stakeholders file
    if os.path.exists(stakeholders_file):
        print(f"\n✅ Found: aggregated_stakeholders.json")
        try:
            with open(stakeholders_file, 'r') as f:
                stakeholders_data = json.load(f)
            stats = stakeholders_data.get('stats', {})
            stakeholders = stakeholders_data.get('stakeholders', [])
            print(f"   - Total stakeholders: {stats.get('total_stakeholders', 0)}")
            print(f"   - Avg projects per person: {stats.get('avg_projects_per_person', 0)}")
            print(f"   - Processing time: {stats.get('processing_time_ms', 0)}ms")

            if stakeholders and len(stakeholders) > 0:
                print(f"\n   Top 3 stakeholders (by message count):")
                for i, person in enumerate(stakeholders[:3], 1):
                    roles = person.get('inferred_roles', [])
                    primary_role = person.get('primary_role', 'Unknown')
                    print(f"     {i}. {person['name']} ({person['email']})")
                    print(f"        - Primary role: {primary_role}")
                    print(f"        - Messages: {person['message_count']}")
                    print(f"        - Projects: {len(person['projects'])}")
        except Exception as e:
            print(f"   ⚠️  Error reading stakeholders file: {e}")
    else:
        print(f"❌ Missing: aggregated_stakeholders.json")

    return os.path.exists(projects_file) and os.path.exists(stakeholders_file)


def validate_clustering_quality():
    """Perform simple validation on clustering results"""
    print("\n" + "="*80)
    print(" 6. VALIDATING CLUSTERING QUALITY")
    print("="*80)

    projects_file = os.path.join("./data", "aggregated_projects.json")

    if not os.path.exists(projects_file):
        print("❌ Cannot validate: aggregated_projects.json not found")
        return False

    try:
        with open(projects_file, 'r') as f:
            projects_data = json.load(f)

        projects = projects_data.get('projects', [])
        stats = projects_data.get('stats', {})

        print(f"\n✅ Clustering Quality Checks:")
        print(f"   - Projects found: {len(projects)}")
        print(f"   - Aliases merged: {stats.get('total_aliases_merged', 0)}")

        # Check if clustering actually happened
        aliases_per_project = stats.get('total_aliases_merged', 0) / len(projects) if projects else 0
        if aliases_per_project > 1:
            print(f"   ✅ Clustering active: avg {aliases_per_project:.1f} aliases per project")
        else:
            print(f"   ⚠️  Minimal clustering: {aliases_per_project:.1f} aliases per project")

        # Check confidence distribution
        high_conf = sum(1 for p in projects if p['avg_confidence'] >= 0.80)
        med_conf = sum(1 for p in projects if 0.50 <= p['avg_confidence'] < 0.80)
        low_conf = sum(1 for p in projects if p['avg_confidence'] < 0.50)

        print(f"\n   Confidence Distribution:")
        print(f"   - High (≥0.80): {high_conf} projects")
        print(f"   - Medium (0.50-0.80): {med_conf} projects")
        print(f"   - Low (<0.50): {low_conf} projects")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def main():
    print("\n" + "="*80)
    print(" SIFT AGGREGATION PIPELINE TEST")
    print("="*80)
    print("\nPrerequisites:")
    print("  1. SSH tunnel active: ssh -L 5000:localhost:5000 user@server")
    print("  2. Backend running: python main.py")
    print("  3. PST file parsed with enrichment complete")
    print("="*80)

    verbose = "--verbose" in sys.argv

    # Run tests
    if not test_backend_ready():
        print("\n❌ Backend not ready. Exiting.")
        return False

    if not test_enriched_messages():
        print("\n❌ No enriched messages. Exiting.")
        return False

    job_id = test_start_aggregation()
    if not job_id:
        print("\n❌ Could not start aggregation. Exiting.")
        return False

    result = monitor_aggregation(job_id)
    if not result:
        print("\n⚠️  Aggregation monitoring ended")
    else:
        if check_output_files():
            print("\n✅ Output files created successfully")
            validate_clustering_quality()
        else:
            print("\n⚠️  Some output files missing")

    print("\n" + "="*80)
    print(" TEST COMPLETE")
    print("="*80)
    print("\nOutput files location: ./data/")
    print("  - aggregated_projects.json")
    print("  - aggregated_stakeholders.json")
    print("\nNext: Review clustering quality and run Phase 4 (Reporting)\n")

    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
