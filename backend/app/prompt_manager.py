"""
Prompt Manager - Load, validate, and manage LLM prompts

Handles:
- Loading prompt JSON files from prompts/ directory
- Variable substitution ({subject}, {body}, etc.)
- Validation and error handling
- Prompt versioning and metadata
"""
import json
from pathlib import Path
from typing import Dict, Optional
from app.utils import logger, BACKEND_DIR


class Prompt:
    """Represents a single LLM prompt with metadata"""

    def __init__(self, data: dict):
        self.prompt_id = data.get("prompt_id")
        self.version = data.get("version")
        self.task = data.get("task")
        self.description = data.get("description")
        self.template = data.get("prompt_template")
        self.model_params = data.get("model_params", {})
        self.example_output = data.get("example_output")
        self.tags = data.get("tags", [])
        self._raw_data = data

    def substitute_variables(self, message_data: dict) -> str:
        """Substitute variables in prompt template

        Args:
            message_data: Dict with keys like "subject", "body_snippet", "sender_email", etc.

        Returns:
            Prompt with variables substituted
        """
        prompt = self.template

        # Map message fields to prompt variables
        substitutions = {
            "subject": message_data.get("subject", ""),
            "sender_email": message_data.get("sender_email", ""),
            "sender_name": message_data.get("sender_name", ""),
            "recipients": message_data.get("recipients", ""),
            "cc": message_data.get("cc", ""),
            "cc_recipients": message_data.get("cc", ""),  # Alias
            "delivery_date": str(message_data.get("delivery_date", "")),
            "body_snippet": message_data.get("body_snippet", ""),
            "body_full": message_data.get("body_full", ""),
            "message_class": message_data.get("message_class", ""),
        }

        # Replace all variables
        for key, value in substitutions.items():
            placeholder = "{" + key + "}"
            prompt = prompt.replace(placeholder, str(value))

        return prompt

    def __repr__(self):
        return f"Prompt(id={self.prompt_id}, task={self.task}, version={self.version})"


class PromptManager:
    """Manages loading and accessing prompt templates"""

    def __init__(self, prompts_dir: Optional[Path] = None):
        """Initialize PromptManager

        Args:
            prompts_dir: Directory containing prompt JSON files (default: backend/prompts/)
        """
        if prompts_dir is None:
            prompts_dir = BACKEND_DIR / "prompts"

        self.prompts_dir = prompts_dir
        self.prompts: Dict[str, Prompt] = {}
        self._load_prompts()

    def _load_prompts(self):
        """Load all prompt files from prompts directory"""
        if not self.prompts_dir.exists():
            logger.warning(f"Prompts directory not found: {self.prompts_dir}")
            return

        json_files = list(self.prompts_dir.glob("*.json"))
        logger.info(f"Found {len(json_files)} prompt files in {self.prompts_dir}")

        for file_path in json_files:
            try:
                with open(file_path, 'r') as f:
                    data = json.load(f)

                prompt = Prompt(data)
                self.prompts[prompt.prompt_id] = prompt
                logger.info(f"  Loaded: {prompt.prompt_id} (v{prompt.version})")

            except Exception as e:
                logger.error(f"Error loading prompt {file_path}: {e}")

    def get_prompt(self, prompt_id: str) -> Optional[Prompt]:
        """Get a specific prompt by ID

        Args:
            prompt_id: Prompt ID (e.g., "task_a_projects_v1")

        Returns:
            Prompt object or None if not found
        """
        if prompt_id not in self.prompts:
            logger.error(f"Prompt not found: {prompt_id}")
            available = list(self.prompts.keys())
            logger.info(f"Available prompts: {available}")
            return None

        return self.prompts[prompt_id]

    def get_prompts_for_task(self, task_name: str) -> Dict[str, Prompt]:
        """Get all prompts for a specific task

        Args:
            task_name: Task name (e.g., "project_extraction", "stakeholder_detection")

        Returns:
            Dict of prompt_id -> Prompt for this task
        """
        matching = {
            pid: prompt
            for pid, prompt in self.prompts.items()
            if prompt.task == task_name
        }
        return matching

    def list_prompts(self) -> Dict[str, Prompt]:
        """List all loaded prompts

        Returns:
            Dict of prompt_id -> Prompt
        """
        return self.prompts.copy()

    def list_tasks(self) -> list:
        """List all unique task types

        Returns:
            List of task names
        """
        tasks = set(p.task for p in self.prompts.values())
        return sorted(list(tasks))

    def get_default_prompt_for_task(self, task_name: str) -> Optional[Prompt]:
        """Get the default (v1) prompt for a task

        Args:
            task_name: Task name

        Returns:
            Default prompt or None
        """
        prompts = self.get_prompts_for_task(task_name)
        if not prompts:
            return None

        # Sort by version and return latest v1
        v1_prompts = [p for p in prompts.values() if "v1" in p.prompt_id]
        if v1_prompts:
            return v1_prompts[0]

        # Fallback to first available
        return list(prompts.values())[0]

    def reload(self):
        """Reload all prompts from disk (useful for live prompt editing)"""
        self.prompts.clear()
        self._load_prompts()
        logger.info("Prompts reloaded from disk")
