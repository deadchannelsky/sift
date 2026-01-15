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

    # Generic/placeholder names to filter
    GENERIC_NAMES = {
        "john doe", "jane smith", "jane doe", "john smith", "michael chen",
        "emily davis", "alice brown", "bob johnson", "david lee", "sarah johnson",
        "michael johnson", "alice johnson", "corporate stakeholders", "stakeholder"
    }

    def __init__(self, config: Dict = None):
        """Initialize stakeholder aggregator"""
        self.stakeholders: Dict[str, StakeholderProfile] = {}
        self.stats = {"processing_time_ms": 0}
        self.config = config or {}

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

    def _is_generic_name(self, name: str) -> bool:
        """Check if name is a generic/placeholder name"""
        return name.lower() in self.GENERIC_NAMES

    def _get_name_similarity(self, name1: str, name2: str) -> float:
        """
        Calculate name similarity score (0.0-1.0)

        Handles variations like:
        - "Jane Smith" vs "Jane" (partial match)
        - "john.doe@company.com" vs "John Doe" (email vs name)
        """
        n1 = name1.lower().strip()
        n2 = name2.lower().strip()

        # Exact match
        if n1 == n2:
            return 1.0

        # Extract parts (for "Jane Smith" -> ["jane", "smith"])
        parts1 = n1.split()
        parts2 = n2.split()

        # Check if one is a subset of the other (common names match)
        common_parts = set(parts1) & set(parts2)
        if common_parts and len(common_parts) >= min(len(parts1), len(parts2)) - 1:
            return 0.90

        # Substring matching
        if n1 in n2 or n2 in n1:
            return 0.75

        # Use sequence matcher for fuzzy matching
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, n1, n2).ratio()
        return ratio

    def _deduplicate_by_name(self) -> Dict[str, str]:
        """
        Deduplicate stakeholders by name similarity

        Returns:
            Mapping of email -> canonical_email for merging
        """
        config = self.config.get("stakeholder_filtering", {})
        if not config.get("enable_name_deduplication", False):
            return {}

        threshold = config.get("name_similarity_threshold", 0.85)
        name_to_emails: Dict[str, List[str]] = {}
        merge_map = {}

        # Group stakeholders by similar names
        profiles = list(self.stakeholders.values())
        for i, profile1 in enumerate(profiles):
            for profile2 in profiles[i + 1:]:
                similarity = self._get_name_similarity(profile1.name, profile2.name)

                if similarity >= threshold:
                    # Merge the lower-frequency one into the higher-frequency one
                    if profile1.message_count >= profile2.message_count:
                        canonical = profile1.email
                        duplicate = profile2.email
                    else:
                        canonical = profile2.email
                        duplicate = profile1.email

                    merge_map[duplicate] = canonical
                    logger.info(
                        f"Deduplicate: '{profile1.name}' ({profile1.email}) + "
                        f"'{profile2.name}' ({profile2.email}) => {canonical} "
                        f"(similarity={similarity:.2f})"
                    )

        return merge_map

    def _apply_deduplication(self, merge_map: Dict[str, str]):
        """
        Apply email merging based on name similarity

        Combines profiles that should be the same person
        """
        if not merge_map:
            return

        # For each duplicate email, merge into canonical
        for dup_email, canonical_email in merge_map.items():
            if dup_email not in self.stakeholders:
                continue

            dup_profile = self.stakeholders[dup_email]
            canonical_profile = self.stakeholders.get(canonical_email)

            if not canonical_profile:
                # If canonical doesn't exist, just rename the duplicate
                self.stakeholders[canonical_email] = dup_profile
                del self.stakeholders[dup_email]
                dup_profile.email = canonical_email
            else:
                # Merge duplicate into canonical
                canonical_profile.message_count += dup_profile.message_count
                canonical_profile.interaction_types.update(dup_profile.interaction_types)
                canonical_profile.projects.update(dup_profile.projects)

                # Merge roles
                for role in dup_profile.inferred_roles:
                    existing_role_idx = next(
                        (i for i, r in enumerate(canonical_profile.inferred_roles)
                         if r["role"] == role["role"]),
                        None
                    )
                    if existing_role_idx is not None:
                        canonical_profile.inferred_roles[existing_role_idx]["mention_count"] += role["mention_count"]
                        canonical_profile.inferred_roles[existing_role_idx]["confidence"] = max(
                            canonical_profile.inferred_roles[existing_role_idx]["confidence"],
                            role["confidence"]
                        )
                    else:
                        canonical_profile.inferred_roles.append(role)

                # Update date range
                if dup_profile.first_appearance and (not canonical_profile.first_appearance or dup_profile.first_appearance < canonical_profile.first_appearance):
                    canonical_profile.first_appearance = dup_profile.first_appearance
                if dup_profile.last_appearance and (not canonical_profile.last_appearance or dup_profile.last_appearance > canonical_profile.last_appearance):
                    canonical_profile.last_appearance = dup_profile.last_appearance

                # Remove duplicate
                del self.stakeholders[dup_email]

    def to_json(self) -> dict:
        """
        Export all stakeholders as aggregated_stakeholders.json

        Applies filtering and deduplication before export

        Returns:
            JSON-serializable dict with stakeholders and stats
        """
        config = self.config.get("stakeholder_filtering", {})

        # Step 1: Deduplicate by name similarity
        if config.get("enable_name_deduplication", False):
            merge_map = self._deduplicate_by_name()
            self._apply_deduplication(merge_map)

        stakeholders = []
        filtered_count = 0

        for profile in self.stakeholders.values():
            profile_json = profile.to_json()

            # Step 2: Filter by confidence
            if config.get("enable_filtering", False):
                min_conf = config.get("min_role_confidence", 0.70)
                if profile_json["inferred_roles"]:
                    avg_confidence = sum(r["confidence"] for r in profile_json["inferred_roles"]) / len(profile_json["inferred_roles"])
                    if avg_confidence < min_conf:
                        filtered_count += 1
                        continue

                # Step 3: Filter by mention count
                min_mentions = config.get("min_mention_count", 2)
                if profile_json["message_count"] < min_mentions:
                    filtered_count += 1
                    continue

                # Step 4: Filter generic/placeholder names
                if config.get("exclude_generic_names", False):
                    if self._is_generic_name(profile_json["name"]):
                        logger.info(f"Filtering generic name: {profile_json['name']} ({profile_json['email']})")
                        filtered_count += 1
                        continue

            stakeholders.append(profile_json)

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

        logger.info(f"Stakeholder filtering complete: {filtered_count} filtered out, {len(stakeholders)} remaining")

        return {
            "stakeholders": stakeholders,
            "stats": {
                "total_stakeholders": len(stakeholders),
                "total_before_filtering": len(self.stakeholders) + filtered_count,
                "filtered_out": filtered_count,
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
        self.stakeholder_aggregator = StakeholderAggregator(config)
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

            # Log stakeholder confidence distribution
            stakeholder_data = self.stakeholder_aggregator.to_json()["stakeholders"]
            if stakeholder_data:
                role_confidences = []
                for sh in stakeholder_data:
                    for role in sh.get("inferred_roles", []):
                        role_confidences.append(role["confidence"])

                high_conf = sum(1 for c in role_confidences if c >= 0.80)
                medium_conf = sum(1 for c in role_confidences if 0.50 <= c < 0.80)
                low_conf = sum(1 for c in role_confidences if c < 0.50)

                logger.info(
                    f"Stakeholder confidence distribution: "
                    f"High(>=0.80)={high_conf}, Medium(0.50-0.80)={medium_conf}, Low(<0.50)={low_conf}"
                )

                # Show top 10 by total mentions (before filtering)
                top_stakeholders = sorted(
                    stakeholder_data,
                    key=lambda s: s["message_count"],
                    reverse=True
                )[:10]
                logger.info("Top 10 stakeholders (before filtering):")
                for i, sh in enumerate(top_stakeholders, 1):
                    primary_role = sh.get("primary_role", "Unknown")
                    logger.info(
                        f"  {i}. {sh['name']:20} ({sh['email']:35}) | {primary_role:20} | "
                        f"msgs={sh['message_count']}"
                    )

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

                role_confidence = extraction.get("role_confidence", 0.5)
                name = extraction.get("stakeholder", "Unknown")
                role = extraction.get("inferred_role", "Unknown")
                interaction = extraction.get("interaction_type", "stakeholder")

                # Log raw stakeholder extraction before aggregation
                # This helps diagnose hallucinations vs. real recipient emails
                if not hasattr(self, '_stakeholder_log_count'):
                    self._stakeholder_log_count = 0
                    logger.info("=== STAKEHOLDER EXTRACTIONS (RAW) ===")

                if self._stakeholder_log_count < 50:  # Log first 50 for analysis
                    evidence = extraction.get("evidence", [])
                    evidence_str = " | ".join(evidence[:2]) if evidence else "No evidence"
                    logger.info(
                        f"  {name:20} | {email:35} | {role:20} | "
                        f"conf={role_confidence:.2f} | {interaction:15} | {evidence_str[:60]}"
                    )
                    self._stakeholder_log_count += 1

                self.stakeholder_aggregator.add_stakeholder_mention(
                    email=email,
                    name=name,
                    inferred_role=role,
                    role_confidence=role_confidence,
                    interaction_type=interaction,
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

        # Log filtering stats
        stats = stakeholders_data.get("stats", {})
        filtered_out = stats.get("filtered_out", 0)
        total_before = stats.get("total_before_filtering", 0)
        if filtered_out > 0:
            logger.info(
                f"Stakeholder filtering: {filtered_out} removed "
                f"({100*filtered_out/total_before:.1f}% of {total_before} total), "
                f"{len(stakeholders_data['stakeholders'])} remaining"
            )

        # Update stats
        self.stats["projects_found"] = len(projects_data["projects"])
        self.stats["stakeholders_found"] = len(stakeholders_data["stakeholders"])

    def __repr__(self):
        return f"AggregationEngine(db_session={self.db})"
