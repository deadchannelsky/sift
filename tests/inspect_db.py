"""
Inspect SQLite database locally

Copies the database from remote server and displays statistics.
Usage: python inspect_db.py
"""
import sqlite3
import subprocess
import os
from pathlib import Path
from tabulate import tabulate

# Get the database path
BACKEND_DIR = Path(__file__).parent.parent / "backend"
DB_PATH = BACKEND_DIR / "data" / "messages.db"

def check_local_db():
    """Check if database exists locally"""
    if not DB_PATH.exists():
        print(f"❌ Database not found at: {DB_PATH}")
        print("Make sure you've run the backend and parsed a PST file.")
        return False
    return True

def query_db(query, description=""):
    """Execute a query and return results"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row  # Return rows as dicts
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"❌ Query failed: {e}")
        return None

def print_section(title):
    """Print a formatted section header"""
    print("\n" + "=" * 80)
    print(f" {title}")
    print("=" * 80)

def main():
    print("=" * 80)
    print(" SIFT DATABASE INSPECTOR")
    print("=" * 80)
    print(f"\nDatabase: {DB_PATH}")

    if not check_local_db():
        return

    # Basic Statistics
    print_section("📊 DATABASE STATISTICS")

    # Counts
    stats = {
        "Messages": "SELECT COUNT(*) as count FROM messages",
        "Conversations": "SELECT COUNT(*) as count FROM conversations",
        "Processing Jobs": "SELECT COUNT(*) as count FROM processing_jobs",
        "Extractions": "SELECT COUNT(*) as count FROM extractions",
        "Attachments": "SELECT COUNT(*) as count FROM attachments",
    }

    for label, query in stats.items():
        result = query_db(query)
        if result:
            count = result[0]["count"]
            print(f"  {label}: {count:,}")

    # Sample Messages
    print_section("📧 SAMPLE MESSAGES (first 5)")

    messages = query_db("""
        SELECT
            msg_id, subject, sender_email,
            delivery_date, enrichment_status
        FROM messages
        LIMIT 5
    """)

    if messages:
        table_data = [
            [
                row["msg_id"][:8] + "...",
                row["subject"][:50] if row["subject"] else "(no subject)",
                row["sender_email"][:30] if row["sender_email"] else "(no sender)",
                row["delivery_date"][:10] if row["delivery_date"] else "",
                row["enrichment_status"]
            ]
            for row in messages
        ]
        headers = ["Message ID", "Subject", "Sender", "Date", "Status"]
        print(tabulate(table_data, headers=headers, tablefmt="grid"))
    else:
        print("  ❌ No messages found")

    # Sample Conversations
    print_section("💬 SAMPLE CONVERSATIONS (first 5)")

    conversations = query_db("""
        SELECT
            conversation_id, conversation_topic,
            message_count, date_range_start, date_range_end
        FROM conversations
        ORDER BY message_count DESC
        LIMIT 5
    """)

    if conversations:
        table_data = [
            [
                row["conversation_id"][:8] + "...",
                row["conversation_topic"][:50],
                row["message_count"],
                row["date_range_start"][:10] if row["date_range_start"] else "",
                row["date_range_end"][:10] if row["date_range_end"] else ""
            ]
            for row in conversations
        ]
        headers = ["Conv ID", "Topic", "Messages", "Start Date", "End Date"]
        print(tabulate(table_data, headers=headers, tablefmt="grid"))
    else:
        print("  ❌ No conversations found")

    # Enrichment Status
    print_section("🔄 ENRICHMENT STATUS")

    enrichment = query_db("""
        SELECT
            enrichment_status,
            COUNT(*) as count
        FROM messages
        GROUP BY enrichment_status
    """)

    if enrichment:
        table_data = [[row["enrichment_status"], row["count"]] for row in enrichment]
        print(tabulate(table_data, headers=["Status", "Count"], tablefmt="grid"))
    else:
        print("  ❌ No enrichment data")

    # Processing Jobs
    print_section("⚙️  PROCESSING JOBS")

    jobs = query_db("""
        SELECT
            job_id, status, pst_filename,
            total_messages, processed_messages,
            created_at
        FROM processing_jobs
        ORDER BY created_at DESC
        LIMIT 5
    """)

    if jobs:
        table_data = [
            [
                row["job_id"][:8],
                row["status"],
                row["pst_filename"][-30:] if row["pst_filename"] else "",
                row["total_messages"],
                row["processed_messages"],
                row["created_at"][:19] if row["created_at"] else ""
            ]
            for row in jobs
        ]
        headers = ["Job ID", "Status", "File", "Total", "Processed", "Created"]
        print(tabulate(table_data, headers=headers, tablefmt="grid"))
    else:
        print("  ❌ No jobs found")

    # Conversation Size Distribution
    print_section("📈 CONVERSATION SIZE DISTRIBUTION")

    distribution = query_db("""
        SELECT
            CASE
                WHEN message_count <= 3 THEN '3'
                WHEN message_count <= 5 THEN '4-5'
                WHEN message_count <= 10 THEN '6-10'
                WHEN message_count <= 20 THEN '11-20'
                ELSE '20+'
            END as size_range,
            COUNT(*) as count,
            AVG(message_count) as avg_size
        FROM conversations
        GROUP BY size_range
        ORDER BY size_range
    """)

    if distribution:
        table_data = [
            [
                row["size_range"] + " messages",
                row["count"],
                f"{row['avg_size']:.1f}"
            ]
            for row in distribution
        ]
        print(tabulate(table_data, headers=["Size Range", "Conversations", "Avg"], tablefmt="grid"))

    # Summary
    print_section("✅ SUMMARY")
    messages_count = query_db("SELECT COUNT(*) as count FROM messages")[0]["count"]
    conversations_count = query_db("SELECT COUNT(*) as count FROM conversations")[0]["count"]
    pending_count = query_db("SELECT COUNT(*) as count FROM messages WHERE enrichment_status = 'pending'")[0]["count"]

    print(f"\n  Total Messages: {messages_count:,}")
    print(f"  Total Conversations: {conversations_count:,}")
    print(f"  Ready for Enrichment: {pending_count:,}")
    print(f"\n  Database Size: {os.path.getsize(DB_PATH) / 1024 / 1024:.2f} MB")

    print("\n" + "=" * 80)
    print()

if __name__ == "__main__":
    # Check if tabulate is installed
    try:
        import tabulate
    except ImportError:
        print("Installing tabulate for pretty tables...")
        subprocess.check_call(["pip", "install", "tabulate"])

    main()
