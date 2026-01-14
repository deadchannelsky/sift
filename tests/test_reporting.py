"""
Test reporting pipeline

Validates report generation from aggregated data.
Generates Markdown report and CSV exports.
Tests output format and completeness.

Usage:
    python test_reporting.py              # Test reporting
    python test_reporting.py --verbose    # Verbose output
"""
import sys
import io
import os
import json
import csv

# Fix Windows encoding issues with emojis
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Add backend to path so we can import reporter
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'backend'))

from app.reporter import ReporterEngine
from app.utils import logger


def check_aggregated_files():
    """Check if aggregated JSON files exist"""
    print("\n" + "="*80)
    print(" 1. CHECKING AGGREGATED DATA FILES")
    print("="*80)

    # Look in root data/ directory (absolute path from tests/)
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    data_dir = os.path.abspath(data_dir)

    projects_file = os.path.join(data_dir, "aggregated_projects.json")
    stakeholders_file = os.path.join(data_dir, "aggregated_stakeholders.json")

    print(f"\nLooking for data files in: {data_dir}")

    has_projects = os.path.exists(projects_file)
    has_stakeholders = os.path.exists(stakeholders_file)

    if has_projects:
        print(f"✅ Found: aggregated_projects.json")
        try:
            with open(projects_file, 'r') as f:
                projects_data = json.load(f)
            projects = projects_data.get('projects', [])
            stats = projects_data.get('stats', {})
            print(f"   - Projects: {len(projects)}")
            print(f"   - Total aliases merged: {stats.get('total_aliases_merged', 0)}")
        except Exception as e:
            print(f"   ⚠️  Error reading file: {e}")
    else:
        print(f"❌ Missing: aggregated_projects.json")

    if has_stakeholders:
        print(f"✅ Found: aggregated_stakeholders.json")
        try:
            with open(stakeholders_file, 'r') as f:
                stakeholders_data = json.load(f)
            stakeholders = stakeholders_data.get('stakeholders', [])
            stats = stakeholders_data.get('stats', {})
            print(f"   - Stakeholders: {len(stakeholders)}")
            print(f"   - Avg projects per person: {stats.get('avg_projects_per_person', 0):.2f}")
        except Exception as e:
            print(f"   ⚠️  Error reading file: {e}")
    else:
        print(f"❌ Missing: aggregated_stakeholders.json")

    if has_projects and has_stakeholders:
        return True, data_dir
    else:
        print("\n❌ Missing aggregated data files. Run aggregation first using /aggregate endpoint")
        return False, data_dir


def generate_reports(data_dir: str):
    """Generate reports using ReporterEngine"""
    print("\n" + "="*80)
    print(" 2. GENERATING REPORTS")
    print("="*80)

    try:
        # Load config
        config_path = os.path.join(os.path.dirname(__file__), "..", "config.json")
        with open(config_path, 'r') as f:
            config = json.load(f)

        # Generate reports
        reporter = ReporterEngine(config)
        success = reporter.generate_all_reports(data_dir, data_dir)

        if success:
            print("✅ Reports generated successfully")
            stats = reporter.get_report_stats()
            print(f"   - Projects processed: {stats['projects_count']}")
            print(f"   - Stakeholders processed: {stats['stakeholders_count']}")
            print(f"   - Relationships mapped: {stats['relationships_count']}")
            return True
        else:
            print("❌ Report generation failed")
            return False

    except Exception as e:
        print(f"❌ Error generating reports: {e}")
        import traceback
        traceback.print_exc()
        return False


def check_output_files(data_dir: str):
    """Check if report files were created"""
    print("\n" + "="*80)
    print(" 3. CHECKING REPORT OUTPUT FILES")
    print("="*80)

    expected_files = [
        "Q4_2025_Summary.md",
        "projects_summary.csv",
        "stakeholders_summary.csv",
        "project_stakeholder_matrix.csv"
    ]

    all_exist = True
    for filename in expected_files:
        filepath = os.path.join(data_dir, filename)
        if os.path.exists(filepath):
            size_kb = os.path.getsize(filepath) / 1024
            print(f"✅ {filename} ({size_kb:.1f} KB)")
        else:
            print(f"❌ {filename} - NOT FOUND")
            all_exist = False

    return all_exist


def validate_csv_structure():
    """Validate CSV file structure"""
    print("\n" + "="*80)
    print(" 4. VALIDATING CSV STRUCTURE")
    print("="*80)

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    data_dir = os.path.abspath(data_dir)

    # Check projects_summary.csv
    projects_csv = os.path.join(data_dir, "projects_summary.csv")
    if os.path.exists(projects_csv):
        try:
            with open(projects_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            print(f"✅ projects_summary.csv")
            print(f"   - Rows: {len(rows)}")
            if rows:
                print(f"   - Columns: {', '.join(list(rows[0].keys())[:5])}... ({len(rows[0])} total)")
                print(f"   - First row: {rows[0]['canonical_name']} ({rows[0]['total_mentions']} mentions)")
        except Exception as e:
            print(f"❌ Error reading projects CSV: {e}")
    else:
        print(f"⚠️  projects_summary.csv not found")

    # Check stakeholders_summary.csv
    stakeholders_csv = os.path.join(data_dir, "stakeholders_summary.csv")
    if os.path.exists(stakeholders_csv):
        try:
            with open(stakeholders_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            print(f"✅ stakeholders_summary.csv")
            print(f"   - Rows: {len(rows)}")
            if rows:
                print(f"   - Columns: {', '.join(list(rows[0].keys())[:5])}... ({len(rows[0])} total)")
                print(f"   - First row: {rows[0]['name']} ({rows[0]['message_count']} messages)")
        except Exception as e:
            print(f"❌ Error reading stakeholders CSV: {e}")
    else:
        print(f"⚠️  stakeholders_summary.csv not found")

    # Check matrix CSV
    matrix_csv = os.path.join(data_dir, "project_stakeholder_matrix.csv")
    if os.path.exists(matrix_csv):
        try:
            with open(matrix_csv, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            print(f"✅ project_stakeholder_matrix.csv")
            print(f"   - Relationships: {len(rows)}")
            if rows:
                print(f"   - Columns: {', '.join(rows[0].keys())}")
                # Group by project
                projects = set(row['project_canonical_name'] for row in rows)
                print(f"   - Unique projects: {len(projects)}")
        except Exception as e:
            print(f"❌ Error reading matrix CSV: {e}")
    else:
        print(f"⚠️  project_stakeholder_matrix.csv not found")


def validate_markdown_content():
    """Validate Markdown report content"""
    print("\n" + "="*80)
    print(" 5. VALIDATING MARKDOWN REPORT")
    print("="*80)

    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    data_dir = os.path.abspath(data_dir)
    markdown_file = os.path.join(data_dir, "Q4_2025_Summary.md")

    if os.path.exists(markdown_file):
        try:
            with open(markdown_file, 'r', encoding='utf-8') as f:
                content = f.read()

            print(f"✅ Q4_2025_Summary.md")
            lines = content.split('\n')
            print(f"   - Lines: {len(lines)}")

            # Check for key sections
            sections = [
                "# Sift Email Intelligence Report",
                "## Executive Summary",
                "## 🟢 High-Confidence Projects",
                "## 🟡 Medium-Confidence Projects",
                "## 🔴 Low-Confidence Projects",
                "## 👥 Top Stakeholders",
                "## 📊 Project-Stakeholder Involvement Matrix",
                "## 📅 Temporal Analysis"
            ]

            print("\n   Sections found:")
            for section in sections:
                if section in content:
                    print(f"   ✅ {section}")
                else:
                    print(f"   ⚠️  {section}")

            # Sample content
            if "Executive Summary" in content:
                print("\n   Sample content (first 200 chars of Executive Summary):")
                start = content.find("## Executive Summary") + len("## Executive Summary")
                sample = content[start:start+200].strip()[:100]
                print(f"   {sample}...")

        except Exception as e:
            print(f"❌ Error reading Markdown: {e}")
    else:
        print(f"❌ Q4_2025_Summary.md not found")


def main():
    print("\n" + "="*80)
    print(" SIFT REPORTING TEST")
    print("="*80)
    print("\nValidates Markdown and CSV report generation from aggregated data")
    print("="*80)

    # Check aggregated files
    has_files, data_dir = check_aggregated_files()
    if not has_files:
        print("\n❌ Cannot proceed without aggregated data files")
        return False

    # Generate reports
    if not generate_reports(data_dir):
        print("\n❌ Report generation failed")
        return False

    # Check output files
    if not check_output_files(data_dir):
        print("\n⚠️  Some output files missing")

    # Validate CSV structure
    validate_csv_structure()

    # Validate Markdown content
    validate_markdown_content()

    print("\n" + "="*80)
    print(" TEST COMPLETE")
    print("="*80)

    # Show output location
    print(f"\nReport files location: {data_dir}/")
    print("  - Q4_2025_Summary.md (Markdown report)")
    print("  - projects_summary.csv (Projects export)")
    print("  - stakeholders_summary.csv (Stakeholders export)")
    print("  - project_stakeholder_matrix.csv (Relationship matrix)")
    print("\nNext: Review reports and prepare for Phase 5 (Frontend)\n")

    return True


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
