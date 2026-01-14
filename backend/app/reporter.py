"""
Report generation from aggregated project and stakeholder data

Generates:
- Markdown report with executive summary and detailed analysis (Q4_2025_Summary.md)
- CSV exports for Excel/BI tools (projects_summary.csv, stakeholders_summary.csv, project_stakeholder_matrix.csv)
- Statistics on report generation

Data flow:
  1. ReporterEngine loads aggregated_projects.json and aggregated_stakeholders.json
  2. CSVExporter generates 3 CSV files from the data
  3. MarkdownReporter generates a formatted Markdown report
  4. All outputs written to data/ directory
"""
import os
import csv
import json
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from pathlib import Path
from app.utils import logger


def format_date_range(first: Optional[str], last: Optional[str]) -> str:
    """Format date range for display"""
    if not first or not last:
        return "N/A"
    try:
        first_date = datetime.fromisoformat(first).strftime("%b %d, %Y")
        last_date = datetime.fromisoformat(last).strftime("%b %d, %Y")
        return f"{first_date} - {last_date}"
    except (ValueError, TypeError):
        return "N/A"


def format_confidence(confidence: float, distribution: Optional[Dict] = None) -> str:
    """Format confidence score for display"""
    if distribution:
        return f"{confidence:.2f} ({distribution.get('high', 0)} high, {distribution.get('medium', 0)} medium, {distribution.get('low', 0)} low)"
    return f"{confidence:.2f}"


def extract_quarter_year(date_str: Optional[str]) -> Tuple[int, int]:
    """Extract quarter and year from date string (ISO format)

    Returns: (quarter, year) where quarter is 1-4
    """
    if not date_str:
        return (4, 2025)  # Default to Q4 2025

    try:
        dt = datetime.fromisoformat(date_str)
        quarter = (dt.month - 1) // 3 + 1
        return (quarter, dt.year)
    except (ValueError, TypeError):
        return (4, 2025)


def escape_csv_field(field: Optional[str]) -> str:
    """Escape field for CSV output (handle quotes and commas)"""
    if field is None:
        return ""

    field = str(field)
    if "," in field or '"' in field or "\n" in field:
        # Escape quotes by doubling them
        field = field.replace('"', '""')
        # Wrap in quotes
        field = f'"{field}"'
    return field


class CSVExporter:
    """Exports aggregated data to CSV format"""

    def __init__(self, projects: List[Dict], stakeholders: List[Dict]):
        """Initialize with aggregated project and stakeholder data"""
        self.projects = projects
        self.stakeholders = stakeholders

    def export_projects_summary(self, output_path: str) -> int:
        """Generate projects_summary.csv

        Columns: canonical_name, total_mentions, avg_confidence, confidence_high,
                 confidence_medium, confidence_low, aliases_count, stakeholders_count,
                 date_first, date_last, importance_tier, meeting_count

        Returns: Number of projects written
        """
        try:
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "canonical_name", "total_mentions", "avg_confidence",
                    "confidence_high", "confidence_medium", "confidence_low",
                    "aliases_count", "stakeholders_count", "date_first", "date_last",
                    "importance_tier", "meeting_count"
                ])
                writer.writeheader()

                for project in self.projects:
                    dist = project.get("confidence_distribution", {})
                    date_range = project.get("date_range", {})

                    writer.writerow({
                        "canonical_name": project.get("canonical_name", ""),
                        "total_mentions": project.get("total_mentions", 0),
                        "avg_confidence": round(project.get("avg_confidence", 0), 2),
                        "confidence_high": dist.get("high", 0),
                        "confidence_medium": dist.get("medium", 0),
                        "confidence_low": dist.get("low", 0),
                        "aliases_count": len(project.get("aliases", [])),
                        "stakeholders_count": len(project.get("stakeholders", [])),
                        "date_first": date_range.get("first", ""),
                        "date_last": date_range.get("last", ""),
                        "importance_tier": project.get("importance_tier", ""),
                        "meeting_count": project.get("meeting_count", 0)
                    })

            logger.info(f"Exported {len(self.projects)} projects to {output_path}")
            return len(self.projects)

        except IOError as e:
            logger.error(f"Error writing projects CSV: {e}")
            raise

    def export_stakeholders_summary(self, output_path: str) -> int:
        """Generate stakeholders_summary.csv

        Columns: email, name, primary_role, primary_role_confidence, projects_count,
                 message_count, first_appearance, last_appearance, interaction_types

        Returns: Number of stakeholders written
        """
        try:
            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "email", "name", "primary_role", "primary_role_confidence",
                    "projects_count", "message_count", "first_appearance",
                    "last_appearance", "interaction_types"
                ])
                writer.writeheader()

                for stakeholder in self.stakeholders:
                    roles = stakeholder.get("inferred_roles", [])
                    primary_role_conf = 0
                    if roles:
                        primary_role_conf = round(roles[0].get("confidence", 0), 2)

                    interaction_types = stakeholder.get("interaction_types", [])
                    interaction_str = ";".join(interaction_types) if interaction_types else ""

                    writer.writerow({
                        "email": stakeholder.get("email", ""),
                        "name": stakeholder.get("name", ""),
                        "primary_role": stakeholder.get("primary_role", ""),
                        "primary_role_confidence": primary_role_conf,
                        "projects_count": len(stakeholder.get("projects", [])),
                        "message_count": stakeholder.get("message_count", 0),
                        "first_appearance": stakeholder.get("first_appearance", ""),
                        "last_appearance": stakeholder.get("last_appearance", ""),
                        "interaction_types": interaction_str
                    })

            logger.info(f"Exported {len(self.stakeholders)} stakeholders to {output_path}")
            return len(self.stakeholders)

        except IOError as e:
            logger.error(f"Error writing stakeholders CSV: {e}")
            raise

    def export_project_stakeholder_matrix(self, output_path: str) -> int:
        """Generate project_stakeholder_matrix.csv

        Columns: project_canonical_name, stakeholder_email, stakeholder_name,
                 stakeholder_primary_role

        Returns: Number of relationships written
        """
        try:
            rows = []

            for project in self.projects:
                project_name = project.get("canonical_name", "")
                stakeholder_emails = project.get("stakeholders", [])

                for email in stakeholder_emails:
                    # Find stakeholder details
                    stakeholder = None
                    for s in self.stakeholders:
                        if s.get("email") == email:
                            stakeholder = s
                            break

                    if stakeholder:
                        rows.append({
                            "project_canonical_name": project_name,
                            "stakeholder_email": email,
                            "stakeholder_name": stakeholder.get("name", ""),
                            "stakeholder_primary_role": stakeholder.get("primary_role", "")
                        })

            # Sort by project name then email
            rows.sort(key=lambda x: (x["project_canonical_name"], x["stakeholder_email"]))

            with open(output_path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "project_canonical_name", "stakeholder_email", "stakeholder_name",
                    "stakeholder_primary_role"
                ])
                writer.writeheader()
                writer.writerows(rows)

            logger.info(f"Exported {len(rows)} project-stakeholder relationships to {output_path}")
            return len(rows)

        except IOError as e:
            logger.error(f"Error writing matrix CSV: {e}")
            raise


class MarkdownReporter:
    """Generates formatted Markdown report from aggregated data"""

    def __init__(self, projects: List[Dict], stakeholders: List[Dict],
                 stats: Dict, date_range: Dict):
        """Initialize with aggregated data and statistics"""
        self.projects = projects
        self.stakeholders = stakeholders
        self.stats = stats
        self.date_range = date_range

    def generate_report(self) -> str:
        """Generate complete Markdown report

        Returns: Full markdown string
        """
        sections = []

        # Header
        sections.append(self._header())

        # Executive Summary
        sections.append(self._executive_summary())

        # High-confidence projects
        high_conf_projects = [p for p in self.projects if p.get("avg_confidence", 0) >= 0.80]
        if high_conf_projects:
            sections.append(self._projects_section("high", high_conf_projects))

        # Medium-confidence projects
        med_conf_projects = [p for p in self.projects
                            if 0.50 <= p.get("avg_confidence", 0) < 0.80]
        if med_conf_projects:
            sections.append(self._projects_section("medium", med_conf_projects))

        # Low-confidence projects
        low_conf_projects = [p for p in self.projects
                            if p.get("avg_confidence", 0) < 0.50]
        if low_conf_projects:
            sections.append(self._projects_section("low", low_conf_projects))

        # Stakeholders
        if self.stakeholders:
            sections.append(self._stakeholders_section())

        # Matrix
        if self.projects and self.stakeholders:
            sections.append(self._matrix_section())

        # Temporal analysis
        sections.append(self._temporal_analysis())

        return "\n".join(sections)

    def _header(self) -> str:
        """Generate report header"""
        start = self.date_range.get("start", "Unknown")
        end = self.date_range.get("end", "Unknown")
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

        return f"""# Sift Email Intelligence Report

**Period**: {format_date_range(start, end)}
**Generated**: {now}

---"""

    def _executive_summary(self) -> str:
        """Generate executive summary section"""
        proj_stats = self.stats.get("projects", {})
        stake_stats = self.stats.get("stakeholders", {})

        total_projects = proj_stats.get("total_projects", 0)
        total_stakeholders = stake_stats.get("total_stakeholders", 0)
        processing_time_ms = proj_stats.get("processing_time_ms", 0)

        # Count confidence tiers
        high_conf = sum(1 for p in self.projects if p.get("avg_confidence", 0) >= 0.80)
        med_conf = sum(1 for p in self.projects
                      if 0.50 <= p.get("avg_confidence", 0) < 0.80)
        low_conf = sum(1 for p in self.projects if p.get("avg_confidence", 0) < 0.50)

        # Top 3 projects
        top_projects = self.projects[:3] if self.projects else []

        lines = [
            "## Executive Summary",
            "",
            f"📊 **Projects Identified**: {total_projects} total",
            f"- 🟢 High Confidence (≥0.80): {high_conf} projects",
            f"- 🟡 Medium Confidence (0.50-0.79): {med_conf} projects",
            f"- 🔴 Low Confidence (<0.50): {low_conf} projects",
            "",
            f"👥 **Stakeholders Engaged**: {total_stakeholders} people",
            "",
            f"⏱️ **Processing Time**: {processing_time_ms}ms",
        ]

        if top_projects:
            lines.extend(["", "### Top Projects (by mentions)"])
            for i, proj in enumerate(top_projects, 1):
                name = proj.get("canonical_name", "Unknown")
                mentions = proj.get("total_mentions", 0)
                confidence = proj.get("avg_confidence", 0)
                lines.append(f"{i}. **{name}** - {mentions} mentions ({confidence:.2f} confidence)")

        return "\n".join(lines)

    def _projects_section(self, tier: str, projects: List[Dict]) -> str:
        """Generate projects section for a confidence tier"""
        tier_icons = {
            "high": "🟢",
            "medium": "🟡",
            "low": "🔴"
        }
        tier_names = {
            "high": "High-Confidence Projects",
            "medium": "Medium-Confidence Projects",
            "low": "Low-Confidence Projects"
        }

        icon = tier_icons.get(tier, "")
        name = tier_names.get(tier, "Projects")
        threshold = {"high": "≥0.80", "medium": "0.50-0.79", "low": "<0.50"}.get(tier)

        lines = [f"## {icon} {name} ({threshold})", ""]

        for i, project in enumerate(projects, 1):
            canonical = project.get("canonical_name", "Unknown")
            mentions = project.get("total_mentions", 0)
            confidence = project.get("avg_confidence", 0)
            importance = project.get("importance_tier", "COORDINATION")
            meeting_count = project.get("meeting_count", 0)
            date_range = project.get("date_range", {})
            period = format_date_range(date_range.get("first"), date_range.get("last"))
            stakeholders = project.get("stakeholders", [])
            aliases = project.get("aliases", [])
            messages = project.get("messages", [])

            lines.extend([
                f"### {i}. {canonical} ({mentions} mentions, {confidence:.2f} confidence)",
                f"**Importance**: {importance} | **Meetings**: {meeting_count} | **Period**: {period}",
                "",
                f"**Stakeholders**: {', '.join(stakeholders[:3])}{'...' if len(stakeholders) > 3 else ''} ({len(stakeholders)} total)",
                ""
            ])

            if aliases:
                lines.append(f"**Aliases**: {', '.join(aliases)}")
                lines.append("")

            if messages:
                lines.append("**Key Messages**:")
                for msg in messages[:3]:  # Show top 3 messages
                    date_str = msg.get("subject", "Unknown")
                    confidence_msg = msg.get("confidence", 0)
                    evidence = msg.get("evidence", [])
                    evidence_str = evidence[0] if evidence else "No details"
                    lines.append(f"- {date_str} ({confidence_msg:.2f}) - {evidence_str}")
                lines.append("")

        return "\n".join(lines)

    def _stakeholders_section(self) -> str:
        """Generate stakeholders section"""
        lines = ["## 👥 Top Stakeholders (by engagement)", ""]

        for i, stakeholder in enumerate(self.stakeholders[:10], 1):  # Top 10
            email = stakeholder.get("email", "Unknown")
            name = stakeholder.get("name", "Unknown")
            primary_role = stakeholder.get("primary_role", "Unknown")
            roles = stakeholder.get("inferred_roles", [])
            role_conf = round(roles[0].get("confidence", 0), 2) if roles else 0
            role_mentions = roles[0].get("mention_count", 0) if roles else 0
            projects = stakeholder.get("projects", [])
            message_count = stakeholder.get("message_count", 0)
            interaction_types = stakeholder.get("interaction_types", [])

            date_range = {}
            first = stakeholder.get("first_appearance")
            last = stakeholder.get("last_appearance")
            activity_period = format_date_range(first, last)

            lines.extend([
                f"### {i}. {name} ({email})",
                f"**Primary Role**: {primary_role} ({role_conf} confidence, {role_mentions} mentions)",
                "",
                f"**Projects**: {', '.join(projects[:3])}{'...' if len(projects) > 3 else ''} ({len(projects)} total)",
                "",
                f"**Activity**: {message_count} messages | {activity_period}",
                ""
            ])

            if interaction_types:
                lines.append(f"**Interaction Types**: {', '.join(interaction_types)}")
                lines.append("")

        return "\n".join(lines)

    def _matrix_section(self) -> str:
        """Generate project-stakeholder matrix section"""
        lines = ["## 📊 Project-Stakeholder Involvement Matrix", ""]

        # Get top stakeholders
        top_stakeholders = self.stakeholders[:10]
        stakeholder_map = {s.get("email"): s.get("name") for s in top_stakeholders}

        # Build table header
        header = "| Project | " + " | ".join([s[:15] for s in stakeholder_map.values()]) + " |"
        separator = "| " + " | ".join(["-" * max(len(name[:15]), 7) for name in stakeholder_map.values()]) + " |"

        # Adjust for "Project" column width
        separator = "| ------------- | " + separator.split(" | ", 1)[1]

        lines.append(header)
        lines.append(separator)

        # Add rows
        for project in self.projects[:15]:  # Top 15 projects
            project_name = project.get("canonical_name", "")[:20]  # Truncate
            project_stakeholders = set(project.get("stakeholders", []))

            row = f"| {project_name} |"
            for email in stakeholder_map.keys():
                row += " ✓ |" if email in project_stakeholders else "   |"
            lines.append(row)

        return "\n".join(lines)

    def _temporal_analysis(self) -> str:
        """Generate temporal analysis section"""
        lines = ["## 📅 Temporal Analysis", ""]

        # Projects by date
        lines.append("### Projects by First Mention Date")
        lines.append("")

        # Sort projects by date
        projects_by_date = sorted(self.projects,
                                key=lambda p: p.get("date_range", {}).get("first", "9999-12-31"))

        for project in projects_by_date[:10]:
            canonical = project.get("canonical_name", "")
            date_range = project.get("date_range", {})
            first = date_range.get("first", "Unknown")
            mentions = project.get("total_mentions", 0)

            if first != "Unknown":
                first_date = datetime.fromisoformat(first).strftime("%b %d, %Y")
            else:
                first_date = "Unknown"

            lines.append(f"- {first_date}: **{canonical}** ({mentions} mentions)")

        return "\n".join(lines)

    def write_to_file(self, output_path: str) -> None:
        """Write report to file"""
        try:
            report_content = self.generate_report()
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(report_content)
            logger.info(f"Wrote Markdown report to {output_path}")
        except IOError as e:
            logger.error(f"Error writing Markdown report: {e}")
            raise


class ReporterEngine:
    """Main orchestrator for report generation

    Loads aggregated JSON data and generates all report formats
    """

    def __init__(self, config: Dict):
        """Initialize reporter with configuration"""
        self.config = config
        self.projects = []
        self.stakeholders = []
        self.stats = {}

    def load_aggregated_data(self, data_dir: str) -> bool:
        """Load aggregated JSON files

        Args:
            data_dir: Directory containing aggregated_projects.json and aggregated_stakeholders.json

        Returns:
            True if successful, False otherwise
        """
        try:
            projects_file = os.path.join(data_dir, "aggregated_projects.json")
            stakeholders_file = os.path.join(data_dir, "aggregated_stakeholders.json")

            if not os.path.exists(projects_file):
                logger.error(f"Projects file not found: {projects_file}")
                return False

            if not os.path.exists(stakeholders_file):
                logger.error(f"Stakeholders file not found: {stakeholders_file}")
                return False

            with open(projects_file, "r", encoding="utf-8") as f:
                projects_data = json.load(f)

            with open(stakeholders_file, "r", encoding="utf-8") as f:
                stakeholders_data = json.load(f)

            self.projects = projects_data.get("projects", [])
            self.stakeholders = stakeholders_data.get("stakeholders", [])
            self.stats = {
                "projects": projects_data.get("stats", {}),
                "stakeholders": stakeholders_data.get("stats", {})
            }

            logger.info(f"Loaded {len(self.projects)} projects and {len(self.stakeholders)} stakeholders")
            return True

        except Exception as e:
            logger.error(f"Error loading aggregated data: {e}")
            return False

    def generate_all_reports(self, data_dir: str, output_dir: str) -> bool:
        """Generate all report formats

        Args:
            data_dir: Directory containing aggregated JSON files
            output_dir: Directory to write reports to

        Returns:
            True if all reports generated successfully, False otherwise
        """
        try:
            # Load data
            if not self.load_aggregated_data(data_dir):
                return False

            # Ensure output directory exists
            Path(output_dir).mkdir(parents=True, exist_ok=True)

            # Generate CSV exports
            csv_exporter = CSVExporter(self.projects, self.stakeholders)

            projects_csv = os.path.join(output_dir, "projects_summary.csv")
            stakeholders_csv = os.path.join(output_dir, "stakeholders_summary.csv")
            matrix_csv = os.path.join(output_dir, "project_stakeholder_matrix.csv")

            csv_exporter.export_projects_summary(projects_csv)
            csv_exporter.export_stakeholders_summary(stakeholders_csv)
            csv_exporter.export_project_stakeholder_matrix(matrix_csv)

            # Generate Markdown report
            date_range = self.config.get("processing", {}).get("date_range", {})
            quarter, year = extract_quarter_year(date_range.get("start"))
            report_filename = f"Q{quarter}_{year}_Summary.md"

            markdown_reporter = MarkdownReporter(
                self.projects,
                self.stakeholders,
                self.stats,
                date_range
            )

            report_path = os.path.join(output_dir, report_filename)
            markdown_reporter.write_to_file(report_path)

            logger.info(f"Generated all reports in {output_dir}")
            return True

        except Exception as e:
            logger.error(f"Error generating reports: {e}")
            return False

    def get_report_stats(self) -> Dict:
        """Get statistics about generated reports

        Returns:
            Dictionary with file sizes and row counts
        """
        stats = {
            "projects_count": len(self.projects),
            "stakeholders_count": len(self.stakeholders),
            "relationships_count": sum(len(p.get("stakeholders", [])) for p in self.projects)
        }
        return stats
