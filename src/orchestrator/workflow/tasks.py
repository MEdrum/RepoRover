"""
OpenCode task execution.

Thin seam between the phase engine and the session-based OpenCode integration.
"""

from orchestrator.opencode import OpenCodeResult, OpenCodeTask
from orchestrator.state import WorkflowState


async def run_or_poll(manager, workflow: WorkflowState, task: OpenCodeTask) -> OpenCodeResult:
    """Submit a prompt to the repository's OpenCode session, or poll for its result."""
    return await manager.session_manager.run_or_poll(workflow, task.instructions)