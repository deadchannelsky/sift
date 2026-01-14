"""
Aggregation Pipeline - Project clustering and stakeholder deduplication

Handles:
- Clustering similar project names using string similarity
- Deduplicating stakeholders by email address
- Merging role inferences and interaction patterns
- Generating aggregated JSON outputs
"""
import json
import time
from datetime import datetime
from typing import List, Dict, Optional, Set, Tuple
from difflib import SequenceMatcher
from pathlib import Path
from sqlalchemy.orm import Session, joinedload

from app.models import Message, Extraction
from app.utils import logger


class ProjectMention:
    """Single project mention from a message"""

    def __init__(
        self,
        message_id: int,
        msg_id: str,
        subject: str,
        delivery_date: Optional[datetime],
        confidence: float,
        extraction_data: dict,
        importance_tier: Optional[str] = None,
        is_meeting: bool = False,
    ):
        self.message_id = message_id
        self.msg_id = msg_id
        self.subject = subject
        self.delivery_date = delivery_date
        self.confidence = confidence
        self.extraction_data = extraction_data  # Full extraction JSON dict
        self.importance_tier = importance_tier or "COORDINATION"
        self.is_meeting = is_meeting

    def __repr__(self):
        return f"ProjectMention(msg_id={self.msg_id}, confidence={self.confidence:.2f})"


class ProjectCluster:
    """Represents a cluster of similar project names"""

    def __init__(self, initial_name: str):
        self.canonical_name = initial_name
        self.aliases: Set[str] = {initial_name}
        self.project_type = "project"
        self.mentions: List[ProjectMention] = []
        self.stakeholder_emails: Set[str] = set()
        self.importance_tiers: List[str] = []
        self.meeting_count = 0

    def add_mention(self, mention: ProjectMention):
        """Add a mention to this cluster"""
        self.mentions.append(mention)
        self.importance_tiers.append(mention.importance_tier)
        if mention.is_meeting:
            self.meeting_count += 1

    def add_alias(self, alias: str):
        """Add alternate name to this cluster"""
        self.aliases.add(alias)

    def add_stakeholder(self, email: str):
        """Add stakeholder email to this cluster"""
        self.stakeholder_emails.add(email)

    def calculate_stats(self) -> dict:
        """Calculate aggregate statistics for this cluster"""
        if not self.mentions:
            return {
                "total_mentions": 0,
                "avg_confidence": 0.0,
                "confidence_distribution": {"high": 0, "medium": 0, "low": 0},
                "date_range": {"first": None, "last": None},
                "importance_tier": "COORDINATION",
            }

        # Calculate confidence statistics
        confidences = [m.confidence for m in self.mentions]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        # Confidence distribution (using config thresholds: high >= 0.80, medium >= 0.50)
        conf_high = sum(1 for c in confidences if c >= 0.80)
        conf_medium = sum(1 for c in confidences if 0.50 <= c < 0.80)
        conf_low = sum(1 for c in confidences if c < 0.50)

        # Date range
        dates = [m.delivery_date for m in self.mentions if m.delivery_date]
        first_date = min(dates) if dates else None
        last_date = max(dates) if dates else None

        # Most common importance tier
        tier_counts = {}
        for tier in self.importance_tiers:
            tier_counts[tier] = tier_counts.get(tier, 0) + 1
        most_common_tier = (
            max(tier_counts.items(), key=lambda x: x[1])[0]
            if tier_counts
            else "COORDINATION"
        )

        return {
            "total_mentions": len(self.mentions),
            "avg_confidence": round(avg_confidence, 2),
            "confidence_distribution": {
                "high": conf_high,
                "medium": conf_medium,
                "low": conf_low,
            },
            "date_range": {
                "first": first_date.isoformat() if first_date else None,
                "last": last_date.isoformat() if last_date else None,
            },
            "importance_tier": most_common_tier,
        }

    def to_json(self) -> dict:
        """Export cluster as JSON object"""
        stats = self.calculate_stats()

        return {
            "canonical_name": self.canonical_name,
            "aliases": sorted(list(self.aliases)),
            "project_type": self.project_type,
            "total_mentions": stats["total_mentions"],
            "avg_confidence": stats["avg_confidence"],
            "confidence_distribution": stats["confidence_distribution"],
            "messages": [
                {
                    "message_id": m.message_id,
                    "msg_id": m.msg_id[:16] + "...",
                    "subject": m.subject[:80] if m.subject else "(no subject)",
                    "confidence": round(m.confidence, 2),
                    "evidence": m.extraction_data.get("reasoning", []),
                }
                for m in self.mentions
            ],
            "stakeholders": sorted(list(self.stakeholder_emails)),
            "date_range": stats["date_range"],
            "importance_tier": stats["importance_tier"],
            "meeting_count": self.meeting_count,
        }

    def __repr__(self):
        return f"ProjectCluster(name={self.canonical_name}, aliases={len(self.aliases)}, mentions={len(self.mentions)})"


class ProjectClusterer:
    """Clusters similar project names using string similarity"""

    def __init__(self, similarity_threshold: float = 0.75):
        """
        Initialize project clusterer

        Args:
            similarity_threshold: Minimum similarity score to merge projects (0.0-1.0)
        """
        self.similarity_threshold = similarity_threshold
        self.clusters: List[ProjectCluster] = []
        self.stats = {"processing_time_ms": 0}

    def add_project_mention(
        self,
        project_name: str,
        message_id: int,
        msg_id: str,
        subject: str,
        delivery_date: Optional[datetime],
        confidence: float,
        extraction_data: dict,
        importance_tier: Optional[str] = None,
        is_meeting: bool = False,
    ):
        """Add a project mention to clustering system"""
        if not project_name:
            return

        # Create mention object
        mention = ProjectMention(
            message_id=message_id,
            msg_id=msg_id,
            subject=subject,
            delivery_date=delivery_date,
            confidence=confidence,
            extraction_data=extraction_data,
            importance_tier=importance_tier,
            is_meeting=is_meeting,
        )

        # Try to find matching cluster
        cluster = self.find_best_cluster(project_name)

        if cluster is None:
            # Create new cluster
            cluster = ProjectCluster(project_name)
            self.clusters.append(cluster)
            logger.debug(f"Created new cluster: {project_name}")
        else:
            # Add as alias to existing cluster
            cluster.add_alias(project_name)
            logger.debug(f"Added alias '{project_name}' to cluster {cluster.canonical_name}")

        # Add mention to cluster
        cluster.add_mention(mention)

    def calculate_similarity(self, name1: str, name2: str) -> float:
        """
        Calculate string similarity using difflib.SequenceMatcher

        Args:
            name1: First project name
            name2: Second project name

        Returns:
            Similarity score 0.0-1.0
        """
        n1 = self.normalize_project_name(name1)
        n2 = self.normalize_project_name(name2)

        if not n1 or not n2:
            return 0.0

        return SequenceMatcher(None, n1, n2).ratio()

    def normalize_project_name(self, name: str) -> str:
        """
        Normalize project name for comparison

        - Lowercase
        - Strip whitespace
        - Remove common stopwords (the, a, project, initiative, etc)

        Args:
            name: Project name to normalize

        Returns:
            Normalized name
        """
        if not name:
            return ""

        # Lowercase and strip
        normalized = name.lower().strip()

        # Remove common filler words
        stopwords = {
            "the",
            "a",
            "an",
            "project",
            "initiative",
            "program",
            "planning",
            "phase",
            "effort",
            "work",
            "task",
            "plan",
        }

        words = normalized.split()
        filtered = [w for w in words if w not in stopwords]
        return " ".join(filtered)

    def find_best_cluster(self, project_name: str) -> Optional[ProjectCluster]:
        """
        Find existing cluster that matches project name

        Args:
            project_name: Name to match

        Returns:
            Matching ProjectCluster or None if no match above threshold
        """
        best_cluster = None
        best_similarity = self.similarity_threshold

        for cluster in self.clusters:
            # Calculate similarity with canonical name
            similarity = self.calculate_similarity(
                project_name, cluster.canonical_name
            )

            if similarity > best_similarity:
                best_similarity = similarity
                best_cluster = cluster

        return best_cluster

    def select_canonical_name(
        self,
        aliases: List[str],
        mention_counts: Dict[str, int],
        confidence_scores: Dict[str, float],
    ) -> str:
        """
        Choose best canonical name from aliases

        Strategy: Most frequent name with highest average confidence

        Args:
            aliases: List of alternate names
            mention_counts: Count of mentions per name
            confidence_scores: Average confidence per name

        Returns:
            Selected canonical name
        """
        if not aliases:
            return ""

        # Score each alias: (mention_count * 2) + (avg_confidence * 10)
        scores = {}
        for alias in aliases:
            count = mention_counts.get(alias, 0)
            confidence = confidence_scores.get(alias, 0.0)
            score = (count * 2) + (confidence * 10)
            scores[alias] = score

        # Return alias with highest score
        best_alias = max(scores.items(), key=lambda x: x[1])[0]
        return best_alias

    def to_json(self) -> dict:
        """
        Export all clusters as aggregated_projects.json

        Returns:
            JSON-serializable dict with projects and stats
        """
        projects = []

        for cluster in self.clusters:
            projects.append(cluster.to_json())

        # Sort by total mentions (descending)
        projects.sort(key=lambda p: p["total_mentions"], reverse=True)

        # Calculate aggregate stats
        total_aliases_merged = sum(len(p["aliases"]) for p in projects)
        avg_mentions = (
            round(sum(p["total_mentions"] for p in projects) / len(projects), 1)
            if projects
            else 0
        )

        return {
            "projects": projects,
            "stats": {
                "total_projects": len(projects),
                "total_aliases_merged": total_aliases_merged,
                "avg_mentions_per_project": avg_mentions,
                "processing_time_ms": self.stats.get("processing_time_ms", 0),
            },
        }

    def __repr__(self):
        return f"ProjectClusterer(clusters={len(self.clusters)}, threshold={self.similarity_threshold})"


class StakeholderProfile:
    """Aggregated profile for a single stakeholder"""

    def __init__(self, email: str, name: str):
        self.email = email
        self.name = name
        self.inferred_roles: List[dict] = []  # {role, confidence, mention_count}
        self.interaction_types: Set[str] = set()
        self.projects: Set[str] = set()
        self.message_count = 0
        self.first_appearance: Optional[datetime] = None
        self.last_appearance: Optional[datetime] = None

    def add_mention(
        self,
        role: str,
        confidence: float,
        interaction_type: str,
        delivery_date: Optional[datetime],
        project_name: Optional[str] = None,
    ):
        """Update profile with new mention"""
        # Update date range
        if delivery_date:
            if self.first_appearance is None or delivery_date < self.first_appearance:
                self.first_appearance = delivery_date
            if self.last_appearance is None or delivery_date > self.last_appearance:
                self.last_appearance = delivery_date

        # Add interaction type
        if interaction_type:
            self.interaction_types.add(interaction_type)

        # Add project
        if project_name:
            self.projects.add(project_name)

        # Update role (handled by merge_roles in aggregator)
        # This will be called from aggregator after merging

        # Increment message count
        self.message_count += 1

    def to_json(self) -> dict:
        """Export stakeholder as JSON object"""
        return {
            "email": self.email,
            "name": self.name,
            "inferred_roles": [
                {
                    "role": r["role"],
                    "confidence": round(r["confidence"], 2),
                    "mention_count": r["mention_count"],
                }
                for r in self.inferred_roles
            ],
            "primary_role": (
                self.inferred_roles[0]["role"] if self.inferred_roles else "Unknown"
            ),
            "interaction_types": sorted(list(self.interaction_types)),
            "projects": sorted(list(self.projects)),
            "message_count": self.message_count,
            "first_appearance": (
                self.first_appearance.isoformat() if self.first_appearance else None
            ),
            "last_appearance": (
                self.last_appearance.isoformat() if self.last_appearance else None
            ),
        }

    def __repr__(self):
        return f"StakeholderProfile(email={self.email}, name={self.name}, message_count={self.message_count})"


class StakeholderAggregator:
    """Deduplicates stakeholders by email and aggregates their data"""

    def __init__(self):
        """Initialize stakeholder aggregator"""
        self.stakeholders: Dict[str, StakeholderProfile] = {}
        self.stats = {"processing_time_ms": 0}

    def add_stakeholder_mention(
        self,
        email: str,
        name: str,
        inferred_role: str,
        role_confidence: float,
        interaction_type: str,
        message_id: int,
        delivery_date: Optional[datetime],
        project_name: Optional[str] = None,
    ):
        """
        Add or update stakeholder profile

        Args:
            email: Email address (canonical key)
            name: Person's name
            inferred_role: Job function (PM, Engineer, etc)
            role_confidence: Confidence in role (0.0-1.0)
            interaction_type: Behavioral pattern (initiator, responder, etc)
            message_id: Message this mention came from
            delivery_date: Message delivery date
            project_name: Associated project if known
        """
        if not email:
            return

        # Get or create stakeholder profile
        if email not in self.stakeholders:
            self.stakeholders[email] = StakeholderProfile(email, name)

        profile = self.stakeholders[email]

        # Merge role information
        profile.inferred_roles = self.merge_roles(
            profile.inferred_roles, inferred_role, role_confidence
        )

        # Update primary role
        if profile.inferred_roles:
            profile.inferred_roles.sort(
                key=lambda r: r["confidence"] * r["mention_count"],
                reverse=True,
            )

        # Add mention details
        profile.add_mention(
            role=inferred_role,
            confidence=role_confidence,
            interaction_type=interaction_type,
            delivery_date=delivery_date,
            project_name=project_name,
        )

    def merge_roles(
        self,
        existing_roles: List[dict],
        new_role: str,
        confidence: float,
    ) -> List[dict]:
        """
        Merge role inference with existing roles

        Track all mentioned roles with mention counts

        Args:
            existing_roles: Current list of role dicts
            new_role: New role to add/merge
            confidence: Confidence in new role

        Returns:
            Updated roles list
        """
        if not new_role:
            return existing_roles

        # Look for existing role entry
        for role_dict in existing_roles:
            if role_dict["role"] == new_role:
                # Update existing role
                role_dict["mention_count"] += 1
                # Update confidence (average with existing)
                role_dict["confidence"] = (
                    role_dict["confidence"] + confidence
                ) / 2
                return existing_roles

        # New role, add it
        existing_roles.append(
            {"role": new_role, "confidence": confidence, "mention_count": 1}
        )
        return existing_roles

    def select_primary_role(self, roles: List[dict]) -> str:
        """
        Choose primary role based on highest total confidence * mention_count

        Args:
            roles: List of role dicts

        Returns:
            Primary role name
        """
        if not roles:
            return "Unknown"

        # Score by confidence * mention_count
        scored = [
            (r["role"], r["confidence"] * r["mention_count"]) for r in roles
        ]
        best_role = max(scored, key=lambda x: x[1])[0] if scored else "Unknown"
        return best_role

    def to_json(self) -> dict:
        """
        Export all stakeholders as aggregated_stakeholders.json

        Returns:
            JSON-serializable dict with stakeholders and stats
        """
        stakeholders = []

        for profile in self.stakeholders.values():
            stakeholders.append(profile.to_json())

        # Sort by message count (descending)
        stakeholders.sort(key=lambda s: s["message_count"], reverse=True)

        # Calculate aggregate stats
        avg_projects = (
            round(
                sum(len(s["projects"]) for s in stakeholders) / len(stakeholders),
                1,
            )
            if stakeholders
            else 0
        )

        return {
            "stakeholders": stakeholders,
            "stats": {
                "total_stakeholders": len(stakeholders),
                "avg_projects_per_person": avg_projects,
                "processing_time_ms": self.stats.get("processing_time_ms", 0),
            },
        }

    def __repr__(self):
        return f"StakeholderAggregator(stakeholders={len(self.stakeholders)})"


class AggregationEngine:
    """Main orchestrator for aggregation pipeline"""

    def __init__(self, db_session: Session, config: Dict):
        """
        Initialize aggregation engine

        Args:
            db_session: SQLAlchemy session for database access
            config: Config dict with settings
        """
        self.db = db_session
        self.config = config
        self.project_clusterer = ProjectClusterer(
            similarity_threshold=config.get("clustering", {}).get(
                "embedding_similarity_threshold", 0.75
            )
        )
        self.stakeholder_aggregator = StakeholderAggregator()
        self.stats = {
            "messages_processed": 0,
            "projects_found": 0,
            "projects_clustered": 0,
            "stakeholders_found": 0,
            "errors": 0,
            "processing_time_ms": 0,
        }

    def run_aggregation(self) -> Dict:
        """
        Main entry point - runs full aggregation pipeline

        Returns:
            Stats dict with processing results
        """
        start_time = time.time()
        logger.info("Starting aggregation pipeline")

        try:
            # Load and process all enriched messages
            self.load_and_process_extractions()

            # Calculate processing time
            processing_time = int((time.time() - start_time) * 1000)
            self.stats["processing_time_ms"] = processing_time

            logger.info(
                f"Aggregation complete: "
                f"messages={self.stats['messages_processed']}, "
                f"projects={len(self.project_clusterer.clusters)}, "
                f"stakeholders={len(self.stakeholder_aggregator.stakeholders)}, "
                f"errors={self.stats['errors']}, "
                f"time={processing_time}ms"
            )

            return self.stats

        except Exception as e:
            logger.error(f"Aggregation pipeline failed: {e}")
            raise

    def load_and_process_extractions(self):
        """
        Query all extractions from database and process by message

        Loads all completed messages with their extractions
        """
        try:
            messages = (
                self.db.query(Message)
                .filter(Message.enrichment_status == "completed")
                .options(joinedload(Message.extractions))
                .order_by(Message.delivery_date)
                .all()
            )

            logger.info(f"Loaded {len(messages)} enriched messages for aggregation")

            for message in messages:
                # Group extractions by task_name
                extractions_dict = {
                    ext.task_name: ext
                    for ext in message.extractions
                }

                self.process_message_extractions(message, extractions_dict)

        except Exception as e:
            logger.error(f"Error loading extractions: {e}")
            raise

    def process_message_extractions(
        self,
        message: Message,
        extractions: Dict[str, Extraction],
    ):
        """
        Process all 4 extraction types for a single message

        Args:
            message: Message object
            extractions: Dict mapping task_name -> Extraction object
        """
        # Parse all 4 extraction JSONs
        task_a = self.parse_extraction_json(extractions.get("task_a_projects"))  # Projects
        task_b = self.parse_extraction_json(extractions.get("task_b_stakeholders"))  # Stakeholders
        task_c = self.parse_extraction_json(extractions.get("task_c_importance"))  # Importance
        task_d = self.parse_extraction_json(extractions.get("task_d_meetings"))  # Meetings

        # Skip if critical tasks failed
        if not task_a or not task_b:
            self.stats["errors"] += 1
            return

        # Extract key data
        importance_tier = task_c.get("importance_tier", "COORDINATION") if task_c else "COORDINATION"
        is_meeting = task_d.get("is_meeting_related", False) if task_d else False
        primary_project = task_a.get("most_likely_project")

        # Process project extractions
        if task_a.get("extractions"):
            for extraction in task_a["extractions"]:
                project_name = extraction.get("extraction")
                if not project_name:
                    continue

                self.project_clusterer.add_project_mention(
                    project_name=project_name,
                    message_id=message.id,
                    msg_id=message.msg_id,
                    subject=message.subject,
                    delivery_date=message.delivery_date,
                    confidence=extraction.get("confidence", 0.5),
                    extraction_data=extraction,
                    importance_tier=importance_tier,
                    is_meeting=is_meeting,
                )

        # Process stakeholder extractions
        if task_b.get("extractions"):
            for extraction in task_b["extractions"]:
                email = extraction.get("email")
                if not email:
                    continue

                self.stakeholder_aggregator.add_stakeholder_mention(
                    email=email,
                    name=extraction.get("stakeholder", "Unknown"),
                    inferred_role=extraction.get("inferred_role", "Unknown"),
                    role_confidence=extraction.get("role_confidence", 0.5),
                    interaction_type=extraction.get("interaction_type", "stakeholder"),
                    message_id=message.id,
                    delivery_date=message.delivery_date,
                    project_name=primary_project,
                )

                # Add stakeholder email to project cluster if project exists
                if primary_project:
                    cluster = self.project_clusterer.find_best_cluster(primary_project)
                    if cluster:
                        cluster.add_stakeholder(email)

        self.stats["messages_processed"] += 1

    def parse_extraction_json(self, extraction: Extraction) -> Optional[dict]:
        """
        Safely parse extraction_json with error handling

        Args:
            extraction: Extraction object

        Returns:
            Parsed JSON dict or None if error/malformed
        """
        if not extraction:
            return None

        if extraction.confidence == "error":
            return None

        try:
            data = json.loads(extraction.extraction_json)
            return data
        except json.JSONDecodeError as e:
            logger.warning(
                f"Malformed JSON for message_id={extraction.message_id}, "
                f"task={extraction.task_name}: {e}"
            )
            self.stats["errors"] += 1
            return None
        except Exception as e:
            logger.error(f"Unexpected error parsing extraction: {e}")
            self.stats["errors"] += 1
            return None

    def write_json_outputs(self, output_dir: str):
        """
        Write aggregated_projects.json and aggregated_stakeholders.json

        Args:
            output_dir: Directory to write JSON files to
        """
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

        # Write projects
        projects_data = self.project_clusterer.to_json()
        projects_file = output_path / "aggregated_projects.json"
        with open(projects_file, "w", encoding="utf-8") as f:
            json.dump(projects_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Wrote {projects_file}")

        # Write stakeholders
        stakeholders_data = self.stakeholder_aggregator.to_json()
        stakeholders_file = output_path / "aggregated_stakeholders.json"
        with open(stakeholders_file, "w", encoding="utf-8") as f:
            json.dump(stakeholders_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Wrote {stakeholders_file}")

        # Update stats
        self.stats["projects_found"] = len(projects_data["projects"])
        self.stats["stakeholders_found"] = len(stakeholders_data["stakeholders"])

    def __repr__(self):
        return f"AggregationEngine(db_session={self.db})"
