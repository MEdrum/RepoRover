"""
RepoRover MCP server.

Hosts the agent-facing tools over Model Context Protocol (streamable HTTP).
The agent calls these instead of asking the user directly, so RepoRover stays
the single control plane for the issue workflow.
"""

import logging

from mcp.server.mcpserver import MCPServer

from orchestrator.workflow.manager import WorkflowManager

logger = logging.getLogger(__name__)


class RepoRoverMCPServer:
    """Thin MCP wrapper around the WorkflowManager's report tools."""

    def __init__(self, manager: WorkflowManager):
        """Register the report_done / report_stuck tools on the MCP server."""
        self.manager = manager
        self.server = MCPServer(name="RepoRover")
        self.server.add_tool(
            self.report_done,
            name="report_done",
            description=(
                "Report that a coding step is complete. RepoRover stores the result, "
                "advances its workflow state machine, and starts the next step."
            ),
        )
        self.server.add_tool(
            self.report_stuck,
            name="report_stuck",
            description=(
                "Report that user input is needed (e.g. a design decision). RepoRover "
                "adds the stuck label, posts your description to the issue, and waits "
                "for a human reply before resuming you."
            ),
        )

    async def report_done(self, repository: str, issue_id: int, result: str) -> str:
        """Complete the current step and advance the workflow."""
        outcome = await self.manager.report_done(repository, issue_id, result)
        if outcome.success:
            step = outcome.next_phase.value if outcome.next_phase else "next"
            return f"Step completed for {repository}#{issue_id}; advanced to {step}."
        return f"report_done failed: {outcome.error}"

    async def report_stuck(self, repository: str, issue_id: int, block_description: str) -> str:
        """Mark the workflow as stuck and request user input."""
        outcome = await self.manager.report_stuck(repository, issue_id, block_description)
        if outcome.success:
            return (
                f"Marked {repository}#{issue_id} as stuck and posted the request to the issue; awaiting a human reply."
            )
        return f"report_stuck failed: {outcome.error}"

    async def run(self, host: str, port: int, path: str = "/mcp") -> None:
        """Serve the MCP endpoint over streamable HTTP until cancelled."""
        logger.info(f"Starting RepoRover MCP server on http://{host}:{port}{path}")
        await self.server.run_streamable_http_async(host=host, port=port, streamable_http_path=path)
