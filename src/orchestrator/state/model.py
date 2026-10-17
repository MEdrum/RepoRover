"""
Workflow state model.

Defines the workflow phases, the durable per-issue state, and the on-disk
schema version plus migrations.
"""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

# Bump when the on-disk format changes incompatibly.
SCHEMA_VERSION = 1


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
    # The OpenCode session id for this issue's repository (session-per-repo),
    # persisted so in-flight work survives restarts.
    opencode_session_id: str = ""
    # True once the current task-phase prompt has been submitted and the
    # workflow is awaiting the agent's report_done/report_stuck signal.
    awaiting_agent: bool = False
    # When the prompt for the current phase was submitted (watchdog reference).
    opencode_prompt_sent_at: datetime | None = None
    # Set when the agent reports stuck: the phase to return to on resume, and
    # the human's response to inject once a reply arrives.
    stuck_phase: str = ""
    stuck_response: str = ""
    started_at: datetime | None = None
    updated_at: datetime | None = None
    stuck_reason: str = ""
    retry_count: int = 0
    last_error: str = ""
    retry_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a JSON-friendly dict."""
        data = asdict(self)
        data["phase"] = self.phase.value
        data["started_at"] = _iso(self.started_at)
        data["updated_at"] = _iso(self.updated_at)
        data["retry_at"] = _iso(self.retry_at)
        data["opencode_prompt_sent_at"] = _iso(self.opencode_prompt_sent_at)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "WorkflowState":
        """Deserialize from a JSON-friendly dict."""
        data = dict(data)
        data["phase"] = WorkflowPhase(data["phase"])
        data["started_at"] = _parse(data.get("started_at"))
        data["updated_at"] = _parse(data.get("updated_at"))
        data["retry_at"] = _parse(data.get("retry_at"))
        data["opencode_prompt_sent_at"] = _parse(data.get("opencode_prompt_sent_at"))
        return cls(**data)


def _iso(value: datetime | None) -> str | None:
    """Serialize a datetime to an ISO-8601 string, or None."""
    return value.isoformat() if value is not None else None


def _parse(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string (or datetime) into a timezone-aware datetime."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _migrate_workflow(raw: dict) -> dict:
    """Upgrade a single workflow dict from older on-disk formats."""
    data = dict(raw)
    # v0 -> v1: the run id was renamed to a session id.
    if "opencode_run_id" in data:
        data.setdefault("opencode_session_id", data["opencode_run_id"])
        data.pop("opencode_run_id", None)
    return data


def migrate_workflows(raw: dict) -> dict:
    """Upgrade a raw workflows mapping to the current schema.

    Accepts either the current wrapped form
    `{"schema_version": 1, "workflows": {...}}` or the legacy flat form
    `{"owner/repo#issue": {...}, ...}` and returns the current wrapped form.
    """
    if isinstance(raw, dict) and "workflows" in raw:
        return {
            "schema_version": SCHEMA_VERSION,
            "workflows": {k: _migrate_workflow(v) for k, v in raw.get("workflows", {}).items()},
        }
    # Legacy flat form.
    return {
        "schema_version": SCHEMA_VERSION,
        "workflows": {k: _migrate_workflow(v) for k, v in raw.items()},
    }
