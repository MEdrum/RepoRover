"""
OpenCode task execution.

Submits the phase prompt to the repository's session. Step completion is
signalled by the agent via the MCP tools (report_done / report_stuck), so no
session output polling is needed here; the SessionManager's status endpoint
feeds the inactivity watchdog.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orchestrator.opencode import OpenCodeResult, OpenCodeTask
from orchestrator.state import WorkflowState

if TYPE_CHECKING:
    from orchestrator.workflow.manager import WorkflowManager


async def run_or_poll(manager: WorkflowManager, workflow: WorkflowState, task: OpenCodeTask) -> OpenCodeResult:
    """Submit the phase prompt to the repository's OpenCode session."""
    return await manager.session_manager.submit_prompt(workflow, task.instructions)
