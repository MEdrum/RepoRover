"""
OpenCode task execution.

Submits the phase prompt to the repository's session. Step completion is
signalled by the agent via the MCP tools (report_done / report_stuck), so no
session output polling is needed here; the SessionManager's status endpoint
feeds the inactivity watchdog.
"""

from orchestrator.opencode import OpenCodeResult, OpenCodeTask
from orchestrator.state import WorkflowState


async def run_or_poll(manager, workflow: WorkflowState, task: OpenCodeTask) -> OpenCodeResult:
    """Submit the phase prompt to the repository's OpenCode session."""
    return await manager.session_manager.submit_prompt(workflow, task.instructions)
