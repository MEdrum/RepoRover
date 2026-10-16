"""
Durable orchestrator state store.

Persists workflow state to disk with atomic writes and a bounded JSONL
history.
"""

import contextlib
import json
import logging
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator.state.model import (
    SCHEMA_VERSION,
    WorkflowPhase,
    WorkflowState,
    migrate_workflows,
)

logger = logging.getLogger(__name__)


class OrchestratorState:
    """Manages durable orchestrator state."""

    def __init__(self, state_dir: str, max_history: int = 1000):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workflows_file = self.state_dir / "workflows.json"
        self.history_file = self.state_dir / "history.jsonl"
        self.max_history = max_history
        self._workflows: dict[str, WorkflowState] = {}
        self._history: list[dict[str, Any]] = []

    async def load(self):
        """Load state from disk."""
        if self.workflows_file.exists():
            try:
                with open(self.workflows_file) as f:  # noqa: ASYNC230
                    raw = json.load(f)
                for key, wf_data in migrate_workflows(raw).get("workflows", {}).items():
                    self._workflows[key] = WorkflowState.from_dict(wf_data)
                logger.info(f"Loaded {len(self._workflows)} workflows from state")
            except Exception as e:
                logger.error(f"Failed to load workflows: {e}")

        if self.history_file.exists():
            try:
                count = 0
                with open(self.history_file) as f:  # noqa: ASYNC230
                    for line in f:
                        if line.strip():
                            count += 1
                logger.info(f"Loaded {count} history entries")
            except Exception as e:
                logger.error(f"Failed to load history: {e}")

    async def save(self):
        """Save state to disk atomically."""
        self._save_workflows()
        self._append_history()

    def _save_workflows(self):
        data = {
            "schema_version": SCHEMA_VERSION,
            "workflows": {key: wf.to_dict() for key, wf in self._workflows.items()},
        }
        try:
            fd, tmp = tempfile.mkstemp(dir=str(self.state_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp, self.workflows_file)
            except BaseException:
                with contextlib.suppress(OSError):
                    os.unlink(tmp)
                raise
        except Exception as e:
            logger.error(f"Failed to save workflows: {e}")

    def _append_history(self):
        if not self._history:
            return
        try:
            with open(self.history_file, "a") as f:
                f.writelines(json.dumps(entry) + "\n" for entry in self._history)
            self._history.clear()
            self._trim_history()
        except Exception as e:
            logger.error(f"Failed to save history: {e}")

    def _trim_history(self):
        if self.max_history <= 0:
            return
        if not self.history_file.exists():
            return
        try:
            lines = self.history_file.read_text().splitlines()
            if len(lines) > self.max_history:
                keep = lines[-self.max_history :]
                self.history_file.write_text("\n".join(keep) + ("\n" if keep else ""))
        except Exception as e:
            logger.error(f"Failed to trim history: {e}")

    def get_workflow_key(self, repository: str, issue_number: int) -> str:
        """Generate workflow key."""
        return f"{repository}#{issue_number}"

    def get_workflow(self, repository: str, issue_number: int) -> WorkflowState | None:
        """Get workflow state."""
        key = self.get_workflow_key(repository, issue_number)
        return self._workflows.get(key)

    def set_workflow(self, workflow: WorkflowState):
        """Set workflow state."""
        key = self.get_workflow_key(workflow.repository, workflow.issue_number)
        now = datetime.now(UTC)
        workflow.updated_at = now
        if workflow.started_at is None:
            workflow.started_at = now
        self._workflows[key] = workflow
        self._add_history("workflow_updated", workflow.to_dict())

    def remove_workflow(self, repository: str, issue_number: int):
        """Remove workflow state."""
        key = self.get_workflow_key(repository, issue_number)
        if key in self._workflows:
            del self._workflows[key]
            self._add_history("workflow_removed", {"repository": repository, "issue_number": issue_number})

    def list_active_workflows(self) -> list[WorkflowState]:
        """List all active (non-completed/failed) workflows."""
        return [
            wf for wf in self._workflows.values() if wf.phase not in (WorkflowPhase.COMPLETED, WorkflowPhase.FAILED)
        ]

    def list_workflows(self) -> list[WorkflowState]:
        """List all workflows."""
        return list(self._workflows.values())

    def list_stuck_workflows(self) -> list[WorkflowState]:
        """List stuck workflows."""
        return [wf for wf in self._workflows.values() if wf.phase == WorkflowPhase.STUCK]

    def _add_history(self, event: str, data: dict[str, Any]):
        """Add history entry."""
        self._history.append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "event": event,
                "data": data,
            }
        )

    def get_history(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get recent history."""
        all_history = []
        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    for line in f:
                        if line.strip():
                            all_history.append(json.loads(line))
            except Exception as e:
                logger.debug(f"Failed to read history: {e}")
        all_history.extend(self._history)
        all_history.sort(key=lambda x: x["timestamp"], reverse=True)
        return all_history[:limit]
