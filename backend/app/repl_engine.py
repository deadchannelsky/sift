"""
REPL Engine - Code-based corpus exploration for email analysis

Enables LLM to generate Python code to navigate and analyze
email corpus, demonstrating RLM (Recursive Language Models) approach.
"""

import json
import traceback
from typing import Dict, List, Any, Tuple, Optional
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy.orm import Session

from app.utils import logger


class REPLEngine:
    """
    Executes LLM-generated Python code against email corpus

    Key capabilities:
    - Load corpus from SQLite into queryable dict structure
    - Execute Python code in restricted sandbox
    - Provide helper functions for common operations
    - Track execution trace for visualization
    """

    # Maximum iterations to prevent infinite loops
    MAX_ITERATIONS = 5

    # Maximum output size (10KB)
    MAX_OUTPUT_SIZE = 10240

    def __init__(
        self,
        db_session: Session,
        ollama_client,
        prompt_manager
    ):
        """Initialize REPL engine

        Args:
            db_session: SQLAlchemy database session
            ollama_client: OllamaClient for LLM calls
            prompt_manager: PromptManager for loading prompts
        """
        self.db = db_session
        self.ollama = ollama_client
        self.prompts = prompt_manager
        self.corpus = None
        self.corpus_stats = {}

    def load_corpus(self, force_reload: bool = False) -> Dict:
        """Load all messages + extractions into memory as queryable dicts

        Returns corpus stats dict with:
            - total_messages: int
            - date_range: str
            - unique_senders: int
            - unique_projects: int
        """
        if self.corpus is not None and not force_reload:
            logger.debug("Using cached corpus")
            return self.corpus_stats

        logger.info("Loading corpus into memory...")

        from app.models import Message, Extraction, Conversation

        messages = self.db.query(Message).filter(
            Message.enrichment_status == "completed"
        ).all()

        corpus = []
        all_senders = set()
        all_projects = set()
        dates = []

        for msg in messages:
            # Load extractions for this message
            extractions = self.db.query(Extraction).filter_by(message_id=msg.id).all()
            extraction_data = {}
            projects = []
            stakeholders = []
            importance_tier = None
            is_meeting = False

            for ext in extractions:
                try:
                    data = json.loads(ext.extraction_json)
                    extraction_data[ext.task_name] = data

                    # Extract specific fields for easier querying
                    if ext.task_name == "task_a_projects" and "extractions" in data:
                        for p in data["extractions"]:
                            # Field can be "project" or "extraction" depending on prompt version
                            project_name = p.get("project") or p.get("extraction")
                            if project_name:
                                projects.append(project_name)
                                all_projects.add(project_name.lower())

                    elif ext.task_name == "task_b_stakeholders" and "extractions" in data:
                        for s in data["extractions"]:
                            if s.get("stakeholder"):
                                stakeholders.append({
                                    "name": s["stakeholder"],
                                    "role": s.get("inferred_role", ""),
                                    "confidence": s.get("confidence", "")
                                })

                    elif ext.task_name == "task_c_importance":
                        importance_tier = data.get("importance_tier")

                    elif ext.task_name == "task_d_meetings":
                        is_meeting = data.get("is_meeting_related", False)

                except json.JSONDecodeError:
                    continue

            # Build corpus entry
            entry = {
                "id": msg.id,
                "subject": msg.subject or "",
                "sender_email": msg.sender_email or "",
                "sender_name": msg.sender_name or "",
                "recipients": msg.recipients or "",
                "cc": msg.cc or "",
                "date": msg.delivery_date.isoformat() if msg.delivery_date else "",
                "date_obj": msg.delivery_date,
                "month": msg.delivery_date.strftime("%Y-%m") if msg.delivery_date else "",
                "body": msg.body_full or msg.body_snippet or "",
                "body_snippet": (msg.body_full or msg.body_snippet or "")[:500],
                "projects": projects,
                "stakeholders": stakeholders,
                "importance_tier": importance_tier,
                "is_meeting": is_meeting,
                "raw_extractions": extraction_data
            }

            corpus.append(entry)

            if msg.sender_email:
                all_senders.add(msg.sender_email.lower())
            if msg.delivery_date:
                dates.append(msg.delivery_date)

        self.corpus = corpus

        # Build stats from the final corpus (more reliable than tracking during loop)
        date_range = ""
        if dates:
            min_date = min(dates)
            max_date = max(dates)
            date_range = f"{min_date.strftime('%Y-%m-%d')} to {max_date.strftime('%Y-%m-%d')}"

        # Count unique senders and projects from the built corpus
        final_senders = set()
        final_projects = set()
        for entry in corpus:
            if entry.get("sender_email"):
                final_senders.add(entry["sender_email"].lower())
            for proj in entry.get("projects", []):
                if proj:
                    final_projects.add(proj.lower())

        self.corpus_stats = {
            "total_messages": len(corpus),
            "date_range": date_range,
            "unique_senders": len(final_senders),
            "unique_projects": len(final_projects)
        }

        # Debug: log sample sender data to diagnose why senders might be 0
        if len(final_senders) == 0 and len(corpus) > 0:
            sample_senders = [entry.get("sender_email", "MISSING") for entry in corpus[:5]]
            logger.warning(f"No senders found! Sample sender_email values: {sample_senders}")

        logger.info(f"Corpus loaded: {self.corpus_stats}")
        return self.corpus_stats

    def get_helper_functions(self) -> Dict[str, callable]:
        """Get helper functions available in the sandbox

        These functions are injected into the execution context
        to make common operations easy.
        """

        def group_by_month(messages: List[Dict]) -> Dict[str, List[Dict]]:
            """Group messages by month (YYYY-MM format)"""
            result = defaultdict(list)
            for m in messages:
                month = m.get("month", "unknown")
                result[month].append(m)
            return dict(result)

        def group_by_sender(messages: List[Dict]) -> Dict[str, List[Dict]]:
            """Group messages by sender email"""
            result = defaultdict(list)
            for m in messages:
                sender = m.get("sender_email", "unknown").lower()
                result[sender].append(m)
            return dict(result)

        def group_by_project(messages: List[Dict]) -> Dict[str, List[Dict]]:
            """Group messages by project (message appears in each project it mentions)"""
            result = defaultdict(list)
            for m in messages:
                for project in m.get("projects", []):
                    result[project.lower()].append(m)
            return dict(result)

        def filter_by_date_range(messages: List[Dict], start: str, end: str) -> List[Dict]:
            """Filter messages by date range (YYYY-MM-DD format)"""
            try:
                start_dt = datetime.fromisoformat(start)
                end_dt = datetime.fromisoformat(end)
            except ValueError:
                return messages

            return [
                m for m in messages
                if m.get("date_obj") and start_dt <= m["date_obj"] <= end_dt
            ]

        def filter_by_sender(messages: List[Dict], sender_pattern: str) -> List[Dict]:
            """Filter messages where sender contains pattern (case-insensitive)"""
            pattern = sender_pattern.lower()
            return [
                m for m in messages
                if pattern in m.get("sender_email", "").lower()
                or pattern in m.get("sender_name", "").lower()
            ]

        def filter_by_project(messages: List[Dict], project_pattern: str) -> List[Dict]:
            """Filter messages mentioning project (case-insensitive partial match)"""
            pattern = project_pattern.lower()
            return [
                m for m in messages
                if any(pattern in p.lower() for p in m.get("projects", []))
            ]

        def filter_by_subject(messages: List[Dict], subject_pattern: str) -> List[Dict]:
            """Filter messages where subject contains pattern (case-insensitive)"""
            pattern = subject_pattern.lower()
            return [m for m in messages if pattern in m.get("subject", "").lower()]

        def filter_by_importance(messages: List[Dict], tier: str) -> List[Dict]:
            """Filter messages by importance tier (Critical, Execution, Coordination, FYI, Noise)"""
            return [m for m in messages if m.get("importance_tier", "").lower() == tier.lower()]

        def get_senders(messages: List[Dict]) -> List[str]:
            """Get unique sender emails from messages"""
            return list(set(m.get("sender_email", "") for m in messages if m.get("sender_email")))

        def get_projects(messages: List[Dict]) -> List[str]:
            """Get unique projects mentioned across messages"""
            projects = set()
            for m in messages:
                for p in m.get("projects", []):
                    projects.add(p)
            return list(projects)

        def get_subjects(messages: List[Dict], limit: int = 10) -> List[str]:
            """Get subjects from messages (truncated to 80 chars)"""
            return [m.get("subject", "")[:80] for m in messages[:limit]]

        def count_by_month(messages: List[Dict]) -> Dict[str, int]:
            """Count messages per month"""
            grouped = group_by_month(messages)
            return {month: len(msgs) for month, msgs in sorted(grouped.items())}

        def count_by_sender(messages: List[Dict]) -> Dict[str, int]:
            """Count messages per sender"""
            grouped = group_by_sender(messages)
            return {sender: len(msgs) for sender, msgs in sorted(grouped.items(), key=lambda x: -x[1])}

        def count_by_project(messages: List[Dict]) -> Dict[str, int]:
            """Count messages per project"""
            grouped = group_by_project(messages)
            return {project: len(msgs) for project, msgs in sorted(grouped.items(), key=lambda x: -x[1])}

        def summarize_message(msg: Dict) -> Dict:
            """Get a summary of a single message"""
            return {
                "id": msg.get("id"),
                "date": msg.get("date", "")[:10],
                "sender": msg.get("sender_email", ""),
                "subject": msg.get("subject", "")[:60],
                "projects": msg.get("projects", []),
                "importance": msg.get("importance_tier", "")
            }

        def summarize_messages(messages: List[Dict], limit: int = 10) -> List[Dict]:
            """Get summaries of multiple messages"""
            return [summarize_message(m) for m in messages[:limit]]

        return {
            "group_by_month": group_by_month,
            "group_by_sender": group_by_sender,
            "group_by_project": group_by_project,
            "filter_by_date_range": filter_by_date_range,
            "filter_by_sender": filter_by_sender,
            "filter_by_project": filter_by_project,
            "filter_by_subject": filter_by_subject,
            "filter_by_importance": filter_by_importance,
            "get_senders": get_senders,
            "get_projects": get_projects,
            "get_subjects": get_subjects,
            "count_by_month": count_by_month,
            "count_by_sender": count_by_sender,
            "count_by_project": count_by_project,
            "summarize_message": summarize_message,
            "summarize_messages": summarize_messages,
        }

    def execute_code(self, code: str) -> Tuple[Any, Optional[str]]:
        """Execute Python code in restricted sandbox

        Args:
            code: Python code to execute

        Returns:
            Tuple of (result, error_message)
            - If successful: (result, None)
            - If error: (None, error_message)
        """
        if self.corpus is None:
            self.load_corpus()

        # Build restricted globals
        safe_builtins = {
            'len': len,
            'sum': sum,
            'min': min,
            'max': max,
            'sorted': sorted,
            'list': list,
            'dict': dict,
            'set': set,
            'str': str,
            'int': int,
            'float': float,
            'bool': bool,
            'range': range,
            'enumerate': enumerate,
            'zip': zip,
            'any': any,
            'all': all,
            'abs': abs,
            'round': round,
            'True': True,
            'False': False,
            'None': None,
            # Date/time support for temporal queries
            'datetime': datetime,
            'timedelta': timedelta,
        }

        # Add helper functions
        helpers = self.get_helper_functions()

        # Build execution context
        exec_globals = {
            '__builtins__': safe_builtins,
            'corpus': self.corpus,
            **helpers
        }

        exec_locals = {}

        try:
            # Execute the code
            exec(code, exec_globals, exec_locals)

            # Look for result (last expression or 'result' variable)
            if 'result' in exec_locals:
                result = exec_locals['result']
            elif exec_locals:
                # Return the last assigned variable
                result = list(exec_locals.values())[-1]
            else:
                result = None

            # Truncate large results
            result_str = json.dumps(result, default=str, indent=2)
            if len(result_str) > self.MAX_OUTPUT_SIZE:
                result_str = result_str[:self.MAX_OUTPUT_SIZE] + "\n... (truncated)"
                result = json.loads(result_str.split("\n... (truncated)")[0] + "}")

            return result, None

        except SyntaxError as e:
            return None, f"Syntax error: {e}"
        except NameError as e:
            return None, f"Name error: {e}"
        except TypeError as e:
            return None, f"Type error: {e}"
        except KeyError as e:
            return None, f"Key error: {e}"
        except Exception as e:
            return None, f"Execution error: {type(e).__name__}: {e}"

    def _build_code_gen_prompt(self, user_question: str, previous_steps: List[Dict]) -> str:
        """Build prompt for code generation

        Args:
            user_question: User's natural language question
            previous_steps: List of previous exploration steps

        Returns:
            Full prompt for LLM
        """
        helper_docs = """
Available helper functions:
- group_by_month(messages) -> Dict[str, List[Dict]]  # Group by YYYY-MM
- group_by_sender(messages) -> Dict[str, List[Dict]]  # Group by sender email
- group_by_project(messages) -> Dict[str, List[Dict]]  # Group by project name
- filter_by_date_range(messages, start, end) -> List[Dict]  # Filter by YYYY-MM-DD range
- filter_by_sender(messages, pattern) -> List[Dict]  # Filter by sender (partial match)
- filter_by_project(messages, pattern) -> List[Dict]  # Filter by project (partial match)
- filter_by_subject(messages, pattern) -> List[Dict]  # Filter by subject (partial match)
- filter_by_importance(messages, tier) -> List[Dict]  # Filter by tier (Critical/Execution/Coordination/FYI/Noise)
- get_senders(messages) -> List[str]  # Get unique senders
- get_projects(messages) -> List[str]  # Get unique projects
- get_subjects(messages, limit=10) -> List[str]  # Get subjects (truncated)
- count_by_month(messages) -> Dict[str, int]  # Count per month
- count_by_sender(messages) -> Dict[str, int]  # Count per sender
- count_by_project(messages) -> Dict[str, int]  # Count per project
- summarize_message(msg) -> Dict  # Summary of single message
- summarize_messages(messages, limit=10) -> List[Dict]  # Summaries of messages

Each message in corpus is a dict with:
- id, subject, sender_email, sender_name, recipients, cc
- date (ISO string), date_obj (datetime), month (YYYY-MM)
- body, body_snippet (first 500 chars)
- projects (list of strings)
- stakeholders (list of {name, role, confidence})
- importance_tier (Critical/Execution/Coordination/FYI/Noise)
- is_meeting (bool)
"""

        prompt = f"""You are a Python programmer. Write ONLY Python code, nothing else.

The variable `corpus` is a list of message dicts already loaded in memory.

{helper_docs}

STRICT RULES:
- Output ONLY valid Python code
- NO explanations, NO markdown, NO comments about what you're doing
- NO conversational text like "Yes" or "Would you like..."
- Assign your answer to the variable `result`
- Do NOT use import statements
- Do NOT use print()

Corpus stats: {json.dumps(self.corpus_stats)}

"""

        if previous_steps:
            prompt += "Previous exploration:\n"
            for step in previous_steps:
                prompt += f"\nStep {step['step']}:\n"
                prompt += f"Code:\n{step['code']}\n"
                prompt += f"Result: {json.dumps(step['result'], default=str)[:500]}\n"
            prompt += "\nBased on these results, continue exploring.\n\n"

        prompt += f"Question: {user_question}\n\nPython code:"

        return prompt

    def _build_interpretation_prompt(
        self,
        user_question: str,
        trace: List[Dict]
    ) -> str:
        """Build prompt for interpreting results

        Args:
            user_question: Original user question
            trace: Full execution trace

        Returns:
            Prompt for generating final answer
        """
        prompt = f"""You analyzed an email corpus to answer a question. Here's what you found:

Question: {user_question}

Exploration trace:
"""
        for step in trace:
            prompt += f"\n--- Step {step['step']} ---\n"
            prompt += f"Code executed:\n```python\n{step['code']}\n```\n"
            if step.get('error'):
                prompt += f"Error: {step['error']}\n"
            else:
                result_str = json.dumps(step['result'], default=str, indent=2)
                if len(result_str) > 1000:
                    result_str = result_str[:1000] + "... (truncated)"
                prompt += f"Result:\n{result_str}\n"
            if step.get('interpretation'):
                prompt += f"Intermediate interpretation: {step['interpretation']}\n"

        prompt += """
Based on the exploration above, provide a clear, conversational answer to the user's question.
Cite specific numbers and data from the results.
If the exploration didn't fully answer the question, acknowledge what we found and what's missing.
Format your response for readability (bullet points, sections as needed).
"""

        return prompt

    def query(
        self,
        user_question: str,
        max_iterations: int = 3,
        model_override: Optional[str] = None
    ) -> Dict:
        """Main REPL query loop

        Args:
            user_question: User's natural language question
            max_iterations: Maximum code generation/execution cycles
            model_override: Optional model name to use instead of default

        Returns:
            Dict with:
                - answer: Final interpreted answer
                - trace: List of exploration steps
                - corpus_stats: Corpus statistics
                - model_used: Model name used for generation
        """
        # Ensure corpus is loaded
        self.load_corpus()

        # Track original model to restore after
        original_model = self.ollama.model
        model_used = model_override or original_model

        if model_override:
            logger.info(f"REPL using model override: {model_override}")
            self.ollama.model = model_override

        trace = []

        try:
            for iteration in range(max_iterations):
                step_num = iteration + 1
                logger.info(f"REPL iteration {step_num}/{max_iterations}")

                # Generate code
                code_prompt = self._build_code_gen_prompt(user_question, trace)

                try:
                    generated_code = self.ollama.generate(code_prompt)

                    # Clean up code (remove markdown fences if present)
                    generated_code = generated_code.strip()
                    if generated_code.startswith("```python"):
                        generated_code = generated_code[9:]
                    if generated_code.startswith("```"):
                        generated_code = generated_code[3:]
                    if generated_code.endswith("```"):
                        generated_code = generated_code[:-3]
                    generated_code = generated_code.strip()

                except Exception as e:
                    logger.error(f"Code generation failed: {e}")
                    trace.append({
                        "step": step_num,
                        "code": "",
                        "result": None,
                        "error": f"Code generation failed: {e}",
                        "interpretation": None
                    })
                    break

                # Execute code
                result, error = self.execute_code(generated_code)

                step = {
                    "step": step_num,
                    "code": generated_code,
                    "result": result,
                    "error": error,
                    "interpretation": None
                }

                trace.append(step)

                if error:
                    logger.warning(f"Code execution error: {error}")
                    # Don't continue on error - let final interpretation handle it
                    break

                # Check if we have a meaningful result to stop
                if result is not None:
                    # For simple queries, one iteration is often enough
                    # Could add logic here to decide if more exploration is needed
                    if iteration == 0 and isinstance(result, (dict, list)) and result:
                        # Got a non-empty result, might be enough
                        # In future: ask LLM if more exploration needed
                        pass

                    if iteration >= max_iterations - 1:
                        break

            # Generate final interpretation
            logger.info("Generating final interpretation...")
            interp_prompt = self._build_interpretation_prompt(user_question, trace)

            try:
                answer = self.ollama.generate(interp_prompt)
            except Exception as e:
                logger.error(f"Interpretation generation failed: {e}")
                answer = f"I explored your email corpus but encountered an error generating the final answer: {e}"

            return {
                "answer": answer,
                "trace": trace,
                "corpus_stats": self.corpus_stats,
                "model_used": model_used
            }

        finally:
            # Restore original model
            if model_override:
                self.ollama.model = original_model

    def get_corpus_stats(self) -> Dict:
        """Get corpus statistics (loads corpus if needed)"""
        if self.corpus is None:
            self.load_corpus()
        return self.corpus_stats
