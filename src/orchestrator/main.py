"""
Issue Orchestrator - Main Entry Point

The orchestrator is the control plane for autonomous issue-driven development.
It discovers eligible issues, manages workflow state, and invokes OpenCode.
"""

import asyncio
import logging
import signal
import sys
from pathlib import Path

import yaml

from orchestrator.config import OrchestratorConfig
from orchestrator.discovery import IssueDiscovery
from orchestrator.mcp_server import RepoRoverMCPServer
from orchestrator.opencode import OpenCodeClient
from orchestrator.providers import ProviderManager
from orchestrator.state import OrchestratorState
from orchestrator.workflow import WorkflowManager

logger = logging.getLogger(__name__)


class IssueOrchestrator:
    """Main orchestrator class."""

    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.state = OrchestratorState(config.state.state_dir, config.state.max_history)
        self.providers = ProviderManager(config)
        self.discovery = IssueDiscovery(config, self.state, self.providers)
        self.workflow = WorkflowManager(config, self.state, self.providers)
        self.opencode = OpenCodeClient(config)
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        """Start the orchestrator."""
        logger.info("Starting Issue Orchestrator...")
        self._running = True

        # Load persisted state
        await self.state.load()

        # Reconcile provider-side resources before resuming
        report = await self.workflow.recover()
        if report.get("errors"):
            logger.warning(f"Recovery reported {len(report['errors'])} errors")
        if report.get("missing_branches") or report.get("missing_pull_requests"):
            logger.warning(f"Recovery found missing resources: {report}")

        # Start discovery loop
        discovery_task = asyncio.create_task(self._discovery_loop())
        self._tasks.append(discovery_task)

        # Start workflow processing loop
        workflow_task = asyncio.create_task(self._workflow_loop())
        self._tasks.append(workflow_task)

        # Start the MCP server for the agent's report tools
        if self.config.mcp.enabled:
            self.mcp_server = RepoRoverMCPServer(self.workflow)
            mcp_task = asyncio.create_task(
                self.mcp_server.run(self.config.mcp.host, self.config.mcp.port, self.config.mcp.path)
            )
            self._tasks.append(mcp_task)

        logger.info("Issue Orchestrator started")

    async def stop(self):
        """Stop the orchestrator gracefully."""
        logger.info("Stopping Issue Orchestrator...")
        self._running = False

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()

        # Wait for tasks to complete
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Save state
        await self.state.save()

        # Close owned HTTP clients
        await self.opencode.close()
        await self.workflow.close()

        logger.info("Issue Orchestrator stopped")

    async def _discovery_loop(self):
        """Periodically discover eligible issues."""
        while self._running:
            try:
                await self.discovery.discover_and_queue()
            except Exception as e:
                logger.error(f"Discovery error: {e}")
            try:
                await self.discovery.requeue_stuck_workflows()
            except Exception as e:
                logger.error(f"Stuck workflow check error: {e}")

            # Wait for next poll interval
            await asyncio.sleep(self.config.git_provider.polling_interval)

    async def _workflow_loop(self):
        """Process queued workflows."""
        while self._running:
            try:
                await self.workflow.process_queue()
            except Exception as e:
                logger.error(f"Workflow processing error: {e}")

            # Small delay to prevent busy loop
            await asyncio.sleep(5)

    async def handle_signal(self, sig):
        """Handle shutdown signals."""
        logger.info(f"Received signal {sig.name}")
        await self.stop()


async def main():
    """Main entry point."""
    # Setup logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

    # Load configuration
    config_path = Path("/app/config.yaml")
    if not config_path.exists():  # noqa: ASYNC240
        logger.error(f"Config not found: {config_path}")
        sys.exit(1)

    with open(config_path) as f:  # noqa: ASYNC230 - config is loaded once at startup
        config_data = yaml.safe_load(f)

    config = OrchestratorConfig.from_dict(config_data)

    # Create orchestrator
    orchestrator = IssueOrchestrator(config)

    # Setup signal handlers
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(orchestrator.handle_signal(s)))

    try:
        await orchestrator.start()
        # Keep running until stopped
        while orchestrator._running:  # noqa: ASYNC110
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())
