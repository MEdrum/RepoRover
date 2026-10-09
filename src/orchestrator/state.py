"""
Orchestrator State Management

Handles durable state persistence for crash recovery.
"""

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any
from enum import Enum

logger = logging.getLogger(__name__)


class WorkflowPhase(Enum):
    """Workflow phases."""
    NEW = "new"
    PLANNING = "planning"
    VERIFICATION_DESIGN = "verification_design"
    WRITE_TESTS = "write_tests"
    IMPLEMENTING = "implementing"
    VERIFY = "verify"
    AWAITING_REVIEW = "awaiting_review"
    STUCK = "stuck"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class WorkflowState:
    """State of a single workflow."""
    issue_number: int
    repository: str
    project: str
    phase: WorkflowPhase = WorkflowPhase.NEW
    branch: str = ""
    base_branch: str = "main"
    plan: str = ""
    verification_plan: str = ""
    opencode_run_id: str = ""
    started_at: str = ""
    updated_at: str = ""
    stuck_reason: str = ""
    retry_count: int = 0
    last_error: str = ""
    retry_at: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        data["phase"] = self.phase.value
        return data
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WorkflowState":
        """Create from dictionary."""
        data = data.copy()
        data["phase"] = WorkflowPhase(data["phase"])
        return cls(**data)


class OrchestratorState:
    """Manages durable orchestrator state."""
    
    def __init__(self, state_dir: str):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.workflows_file = self.state_dir / "workflows.json"
        self.history_file = self.state_dir / "history.jsonl"
        self._workflows: Dict[str, WorkflowState] = {}
        self._history: List[Dict[str, Any]] = []
    
    async def load(self):
        """Load state from disk."""
        # Load workflows
        if self.workflows_file.exists():
            try:
                with open(self.workflows_file) as f:
                    data = json.load(f)
                for key, wf_data in data.items():
                    self._workflows[key] = WorkflowState.from_dict(wf_data)
                logger.info(f"Loaded {len(self._workflows)} workflows from state")
            except Exception as e:
                logger.error(f"Failed to load workflows: {e}")
        
        # Load history
        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    for line in f:
                        if line.strip():
                            self._history.append(json.loads(line))
                logger.info(f"Loaded {len(self._history)} history entries")
            except Exception as e:
                logger.error(f"Failed to load history: {e}")
    
    async def save(self):
        """Save state to disk."""
        # Save workflows
        try:
            data = {key: wf.to_dict() for key, wf in self._workflows.items()}
            with open(self.workflows_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save workflows: {e}")
        
        # Save history (append new entries)
        try:
            with open(self.history_file, "a") as f:
                for entry in self._history:
                    f.write(json.dumps(entry) + "\n")
            self._history.clear()
        except Exception as e:
            logger.error(f"Failed to save history: {e}")
    
    def get_workflow_key(self, repository: str, issue_number: int) -> str:
        """Generate workflow key."""
        return f"{repository}#{issue_number}"
    
    def get_workflow(self, repository: str, issue_number: int) -> Optional[WorkflowState]:
        """Get workflow state."""
        key = self.get_workflow_key(repository, issue_number)
        return self._workflows.get(key)
    
    def set_workflow(self, workflow: WorkflowState):
        """Set workflow state."""
        key = self.get_workflow_key(workflow.repository, workflow.issue_number)
        workflow.updated_at = datetime.utcnow().isoformat()
        if not workflow.started_at:
            workflow.started_at = workflow.updated_at
        self._workflows[key] = workflow
        self._add_history("workflow_updated", workflow.to_dict())
    
    def remove_workflow(self, repository: str, issue_number: int):
        """Remove workflow state."""
        key = self.get_workflow_key(repository, issue_number)
        if key in self._workflows:
            del self._workflows[key]
            self._add_history("workflow_removed", {"repository": repository, "issue_number": issue_number})
    
    def list_active_workflows(self) -> List[WorkflowState]:
        """List all active (non-completed/failed) workflows."""
        active = []
        for wf in self._workflows.values():
            if wf.phase not in (WorkflowPhase.COMPLETED, WorkflowPhase.FAILED):
                active.append(wf)
        return active
    
    def list_stuck_workflows(self) -> List[WorkflowState]:
        """List stuck workflows."""
        return [wf for wf in self._workflows.values() if wf.phase == WorkflowPhase.STUCK]
    
    def _add_history(self, event: str, data: Dict[str, Any]):
        """Add history entry."""
        self._history.append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": event,
            "data": data
        })
    
    def get_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent history."""
        # Combine file history with memory history
        all_history = []
        if self.history_file.exists():
            try:
                with open(self.history_file) as f:
                    for line in f:
                        if line.strip():
                            all_history.append(json.loads(line))
            except Exception:
                pass
        all_history.extend(self._history)
        all_history.sort(key=lambda x: x["timestamp"], reverse=True)
        return all_history[:limit]