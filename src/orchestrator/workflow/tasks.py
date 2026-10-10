"""
OpenCode task execution.

The runner is the seam between the phase engine and the OpenCode service. R3
keeps the existing task-based behavior; R4 rewrites this against the real
session API.
"""

from orchestrator.opencode import OpenCodeResult, OpenCodeTask
from orchestrator.state import WorkflowState


async def run_or_poll(manager, workflow: WorkflowState, task: OpenCodeTask) -> OpenCodeResult:
    """Run a task, or poll an in-flight one, persisting its run id.

    If the workflow already has an opencode_session_id the task is polled via
    get_task_status. A missing/unknown task (status None) clears the id and
    submits a fresh task so recovery can self-heal.
    """
    if workflow.opencode_session_id:
        status = await manager.opencode.get_task_status(workflow.opencode_session_id)
        if status is not None:
            return status
        workflow.opencode_session_id = ""
    result = await manager.opencode.run_task(task)
    if result.pending and not workflow.opencode_session_id:
        workflow.opencode_session_id = result.run_id or task.run_id
    return result