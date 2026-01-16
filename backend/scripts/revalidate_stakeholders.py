#!/usr/bin/env python3
"""
Re-validate existing stakeholder extractions against PST recipients

This script cleans up hallucinated stakeholder data in the database by:
1. Loading all stakeholder extractions
2. Checking if extracted email exists in message.recipients or message.cc
3. Removing hallucinated stakeholders that don't appear in actual PST metadata

Run after deploying the stakeholder validation fixes to clean up existing data.

Usage:
    # Dry run (see what would change)
    python backend/scripts/revalidate_stakeholders.py --db data/messages.db

    # Apply changes
    python backend/scripts/revalidate_stakeholders.py --db data/messages.db --apply
"""

import argparse
import json
import logging
import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.models import init_db, get_session, Message, Extraction
from app.utils import logger as base_logger


def setup_logging():
    """Configure logging"""
    handler = logging.StreamHandler()
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    base_logger.addHandler(handler)
    base_logger.setLevel(logging.INFO)


def revalidate_stakeholder_extractions(db_session, dry_run=True):
    """
    Re-validate all stakeholder extractions in database

    Args:
        db_session: SQLAlchemy session
        dry_run: If True, only log what would be changed (don't modify DB)
    """
    base_logger.info("Starting stakeholder re-validation...")

    # Get all stakeholder extractions
    stakeholder_extractions = (
        db_session.query(Extraction)
        .filter(Extraction.task_name == "task_b_stakeholders")
        .all()
    )

    base_logger.info(f"Found {len(stakeholder_extractions)} stakeholder extractions to validate")

    total_modified = 0
    total_stakeholders_rejected = 0
    total_messages_affected = 0

    for extraction in stakeholder_extractions:
        try:
            # Get original message
            message = db_session.query(Message).filter_by(id=extraction.message_id).first()
            if not message:
                base_logger.warning(f"Message not found for extraction {extraction.id}")
                continue

            # Build valid email set
            valid_emails = set()

            if message.sender_email:
                valid_emails.add(message.sender_email.lower().strip())

            if message.recipients:
                for email in message.recipients.split(','):
                    email = email.strip().lower()
                    if email:
                        valid_emails.add(email)

            if message.cc:
                for email in message.cc.split(','):
                    email = email.strip().lower()
                    if email:
                        valid_emails.add(email)

            # Parse extraction JSON
            try:
                extraction_data = json.loads(extraction.extraction_json)
            except json.JSONDecodeError:
                base_logger.warning(f"Invalid JSON in extraction {extraction.id}")
                continue

            if "extractions" not in extraction_data:
                continue

            original_count = len(extraction_data["extractions"])
            filtered_extractions = []
            rejected = []

            # Filter stakeholders
            for stakeholder in extraction_data["extractions"]:
                extracted_email = stakeholder.get("email", "").lower().strip()

                if extracted_email in valid_emails:
                    filtered_extractions.append(stakeholder)
                else:
                    rejected.append({
                        "name": stakeholder.get("stakeholder"),
                        "email": extracted_email
                    })

            # Update if changed
            if len(filtered_extractions) < original_count:
                rejected_count = original_count - len(filtered_extractions)
                total_stakeholders_rejected += rejected_count
                total_modified += 1
                total_messages_affected += 1

                base_logger.info(
                    f"Message {message.msg_id[:16]}: "
                    f"Rejected {rejected_count} hallucinated stakeholders: {rejected}"
                )

                if not dry_run:
                    extraction_data["extractions"] = filtered_extractions
                    extraction.extraction_json = json.dumps(extraction_data)

        except Exception as e:
            base_logger.error(f"Error processing extraction {extraction.id}: {e}", exc_info=True)

    if not dry_run:
        db_session.commit()
        base_logger.info(f"Committed changes to database")
    else:
        base_logger.info(f"DRY RUN - No changes made to database")

    base_logger.info(
        f"Re-validation complete: "
        f"{total_modified} extractions modified across {total_messages_affected} messages, "
        f"{total_stakeholders_rejected} total stakeholders rejected"
    )

    return {
        "extractions_modified": total_modified,
        "messages_affected": total_messages_affected,
        "stakeholders_rejected": total_stakeholders_rejected,
        "dry_run": dry_run
    }


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Re-validate stakeholder extractions against PST recipients",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Dry run - see what would change
  python backend/scripts/revalidate_stakeholders.py --db data/messages.db

  # Apply changes
  python backend/scripts/revalidate_stakeholders.py --db data/messages.db --apply
        """
    )
    parser.add_argument("--db", default="data/messages.db", help="Path to database (default: data/messages.db)")
    parser.add_argument("--apply", action="store_true", help="Actually modify database (default is dry-run)")

    args = parser.parse_args()

    setup_logging()

    try:
        base_logger.info(f"Connecting to database: {args.db}")
        engine = init_db(args.db)
        session = get_session(engine)

        results = revalidate_stakeholder_extractions(session, dry_run=not args.apply)

        # Exit code 0 if no errors
        sys.exit(0)

    except Exception as e:
        base_logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)

    finally:
        if 'session' in locals():
            session.close()


if __name__ == "__main__":
    main()
