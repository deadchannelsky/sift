"""
Post-Aggregation LLM Filter for Project Relevance Assessment

Evaluates aggregated projects for relevance and alignment with user role.
Returns confidence scores and reasoning for filtering decisions.
"""

import json
import time
from typing import Dict, List, Tuple, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.models import ProjectClusterMetadata
from app.utils import logger


class PostAggregationFilter:
    """
    Filter aggregated projects based on user role and LLM relevance assessment

    Workflow:
    1. Load aggregated_projects.json with full metadata
    2. For each project, build rich context dict with full audit trail
    3. Call LLM with context + user_role
    4. Store result (confidence + reasoning) in ProjectClusterMetadata
    5. Return filtered projects list based on confidence threshold
    6. Write filtered outputs to data/
    """

    def __init__(self, session: Session, ollama_client, prompt_manager, config: Dict):
        """Initialize filter with database and LLM access

        Args:
            session: SQLAlchemy database session
            ollama_client: OllamaClient for LLM API calls
            prompt_manager: PromptManager for loading filter prompt
            config: Configuration dict with filter settings
        """
        self.db = session
        self.ollama = ollama_client
        self.prompts = prompt_manager
        self.config = config

        self.stats = {
            "projects_analyzed": 0,
            "projects_relevant": 0,
            "projects_filtered": 0,
            "avg_confidence": 0.0,
            "confidence_distribution": {"high": 0, "medium": 0, "low": 0},
            "processing_time_ms": 0
        }

    def filter_projects(
        self,
        aggregated_projects: List[Dict],
        role_description: str,
        confidence_threshold: float = 0.75
    ) -> Tuple[List[Dict], List[Dict], Dict]:
        """
        Filter aggregated projects based on relevance to user's role

        Args:
            aggregated_projects: List of project dicts from aggregated_projects.json
            role_description: User's role and responsibilities (free-text description)
            confidence_threshold: Min confidence to include project (0.0-1.0)

        Returns:
            Tuple of (included_projects, excluded_projects, filter_results_dict)
        """
        start_time = time.time()
        included_projects = []
        excluded_projects = []
        filter_results = {}

        logger.info(f"Starting post-aggregation filter: role_desc_length={len(role_description)}, threshold={confidence_threshold:.2f}")

        try:
            for project in aggregated_projects:
                try:
                    project_name = project.get("canonical_name", "Unknown")

                    # Call LLM to evaluate relevance
                    confidence, is_relevant, reasoning = self._evaluate_project_relevance(
                        project, role_description
                    )

                    # Store result in database
                    self._save_filter_result(
                        project_name,
                        role_description,
                        confidence,
                        is_relevant,
                        reasoning,
                        confidence < confidence_threshold
                    )

                    # Track statistics
                    self.stats["projects_analyzed"] += 1
                    if confidence >= 0.75:
                        self.stats["confidence_distribution"]["high"] += 1
                    elif confidence >= 0.5:
                        self.stats["confidence_distribution"]["medium"] += 1
                    else:
                        self.stats["confidence_distribution"]["low"] += 1

                    # Filter based on threshold
                    if confidence >= confidence_threshold:
                        included_projects.append(project)
                        self.stats["projects_relevant"] += 1
                        filter_results[project_name] = {
                            "included": True,
                            "confidence": confidence,
                            "reasoning": reasoning
                        }
                    else:
                        excluded_projects.append(project)
                        self.stats["projects_filtered"] += 1
                        filter_results[project_name] = {
                            "included": False,
                            "confidence": confidence,
                            "reasoning": reasoning
                        }

                except Exception as e:
                    logger.error(f"Error evaluating project '{project.get('canonical_name')}': {e}")
                    # Fail-safe: treat as low confidence but continue
                    self.stats["projects_analyzed"] += 1
                    self.stats["projects_filtered"] += 1

            # Calculate average confidence
            if self.stats["projects_analyzed"] > 0:
                total_confidence = sum(r.get("confidence", 0) for r in filter_results.values())
                self.stats["avg_confidence"] = total_confidence / self.stats["projects_analyzed"]

            self.stats["processing_time_ms"] = int((time.time() - start_time) * 1000)

            logger.info(
                f"Filter complete: {self.stats['projects_relevant']} included, "
                f"{self.stats['projects_filtered']} excluded, "
                f"avg_confidence={self.stats['avg_confidence']:.2f}"
            )

            return included_projects, excluded_projects, filter_results

        except Exception as e:
            logger.error(f"Critical error in filter_projects: {e}")
            raise

    def _evaluate_project_relevance(
        self,
        project: Dict,
        role_description: str
    ) -> Tuple[float, bool, List[str]]:
        """
        Use LLM to evaluate project relevance to user's role

        Args:
            project: Project dict from aggregated output
            role_description: User's role and responsibilities (free-text description)

        Returns:
            Tuple of (confidence_score, is_relevant_bool, reasoning_list)
        """
        try:
            # Build rich context for LLM
            context = self._build_project_context(project)

            # Get the filter prompt
            prompt_config = self.config.get("post_aggregation_filter", {})
            prompt_id = prompt_config.get("prompt_id", "task_post_aggregation_filter_v1")
            prompt = self.prompts.get_prompt(prompt_id)

            if not prompt:
                logger.warning(f"Prompt not found: {prompt_id}, using fallback scoring")
                return self._fallback_score(project)

            # Substitute variables in prompt
            filled_prompt = prompt.substitute_variables({
                "user_role": role_description,
                "project_name": project.get("canonical_name", ""),
                "project_aliases": ", ".join(project.get("aliases", [])),
                "importance_tier": project.get("importance_tier", "UNKNOWN"),
                "total_mentions": project.get("total_mentions", 0),
                "avg_confidence": f"{project.get('avg_confidence', 0):.2f}",
                "date_range_first": project.get("date_range", {}).get("first", "Unknown"),
                "date_range_last": project.get("date_range", {}).get("last", "Unknown"),
                "meeting_count": project.get("meeting_count", 0),
                "confidence_high": project.get("confidence_distribution", {}).get("high", 0),
                "confidence_medium": project.get("confidence_distribution", {}).get("medium", 0),
                "confidence_low": project.get("confidence_distribution", {}).get("low", 0),
                "stakeholder_list": self._format_stakeholders(project.get("stakeholders", []))
            })

            # Call LLM
            response = self.ollama.generate(filled_prompt)

            if not response or not response.strip():
                logger.warning("Empty response from LLM, using fallback scoring")
                return self._fallback_score(project)

            # Parse JSON response
            try:
                result = json.loads(response)
                confidence = float(result.get("confidence", 0.5))
                is_relevant = result.get("is_relevant", False)
                reasoning = result.get("reasoning", ["Unable to determine"])

                # Clamp confidence to 0-1 range
                confidence = max(0.0, min(1.0, confidence))

                return confidence, is_relevant, reasoning

            except json.JSONDecodeError as e:
                logger.warning(f"JSON parse error in LLM response: {e}, using fallback")
                return self._fallback_score(project)

        except Exception as e:
            logger.error(f"Error evaluating project relevance: {e}")
            return self._fallback_score(project)

    def _build_project_context(self, project: Dict) -> str:
        """Build rich human-readable context about project for LLM"""
        context = {
            "canonical_name": project.get("canonical_name", "Unknown"),
            "aliases": project.get("aliases", []),
            "importance_tier": project.get("importance_tier", "UNKNOWN"),
            "total_mentions": project.get("total_mentions", 0),
            "avg_confidence": f"{project.get('avg_confidence', 0):.2f}",
            "confidence_distribution": project.get("confidence_distribution", {}),
            "date_range": project.get("date_range", {}),
            "meeting_count": project.get("meeting_count", 0),
            "stakeholders": project.get("stakeholders", [])
        }
        return json.dumps(context, indent=2)

    def _format_stakeholders(self, stakeholder_list: List[Dict]) -> str:
        """Format stakeholder list for LLM consumption"""
        if not stakeholder_list:
            return "No stakeholders identified"

        formatted = []
        for s in stakeholder_list[:10]:  # Limit to top 10 stakeholders
            name = s.get("name", "Unknown")
            email = s.get("email", "")
            roles = s.get("inferred_roles", [])
            role_str = ", ".join([f"{r.get('role')} ({r.get('confidence', 0):.2f})" for r in roles])
            mentions = s.get("mention_count", 0)
            formatted.append(f"- {name} ({email}): {role_str}, {mentions} mentions")

        return "\n".join(formatted)

    def _save_filter_result(
        self,
        project_name: str,
        role_description: str,
        confidence: float,
        is_relevant: bool,
        reasoning: List[str],
        is_filtered: bool
    ):
        """Save filter result to ProjectClusterMetadata table"""
        try:
            # Check if metadata exists
            metadata = self.db.query(ProjectClusterMetadata).filter_by(
                cluster_canonical_name=project_name
            ).first()

            if metadata:
                # Update existing
                metadata.post_agg_filter_enabled = True
                metadata.post_agg_user_role = role_description
                metadata.post_agg_confidence = confidence
                metadata.post_agg_reasoning = json.dumps(reasoning)
                metadata.post_agg_is_relevant = is_relevant
                metadata.post_agg_filtered = is_filtered
                metadata.post_agg_filtered_at = datetime.utcnow() if is_filtered else None
                metadata.post_agg_filter_version = "task_post_aggregation_filter_v1"
                metadata.updated_at = datetime.utcnow()
            else:
                # Create new
                metadata = ProjectClusterMetadata(
                    cluster_canonical_name=project_name,
                    post_agg_filter_enabled=True,
                    post_agg_user_role=role_description,
                    post_agg_confidence=confidence,
                    post_agg_reasoning=json.dumps(reasoning),
                    post_agg_is_relevant=is_relevant,
                    post_agg_filtered=is_filtered,
                    post_agg_filtered_at=datetime.utcnow() if is_filtered else None,
                    post_agg_filter_version="task_post_aggregation_filter_v1"
                )
                self.db.add(metadata)

            self.db.commit()

        except Exception as e:
            logger.error(f"Error saving filter result for '{project_name}': {e}")
            self.db.rollback()

    def _fallback_score(self, project: Dict) -> Tuple[float, bool, List[str]]:
        """
        Fallback scoring when LLM fails

        Uses simple heuristics based on project metadata
        """
        importance = project.get("importance_tier", "FYI")
        mentions = project.get("total_mentions", 0)
        avg_conf = project.get("avg_confidence", 0)

        # Heuristic scoring
        confidence = avg_conf  # Use aggregation confidence as baseline

        # Boost confidence for high-importance projects
        if importance == "CRITICAL":
            confidence = min(1.0, confidence + 0.2)
        elif importance == "EXECUTION":
            confidence = min(1.0, confidence + 0.1)

        # Reduce confidence if very few mentions
        if mentions < 2:
            confidence = max(0.0, confidence - 0.3)

        reasoning = [
            f"Fallback scoring (LLM unavailable)",
            f"Importance tier: {importance}",
            f"Mention count: {mentions}",
            f"Aggregation confidence: {avg_conf:.2f}",
            f"Final confidence: {confidence:.2f}"
        ]

        return confidence, confidence >= 0.5, reasoning
