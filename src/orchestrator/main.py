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
from typing import Optional

import yaml

from orchestrator.config import OrchestratorConfig
from orchestrator.state import OrchestratorState
from orchestrator.discovery import IssueDiscovery
from orchestrator.workflow import WorkflowManager
from orchestrator.opencode import OpenCodeClient

logger = logging.getLogger(__name__)


class IssueOrchestrator:
    """Main orchestrator class."""
    
    def __init__(self, config: OrchestratorConfig):
        self.config = config
        self.state = OrchestratorState(config.state.state_dir)
        self.discovery = IssueDiscovery(config, self.state)
        self.workflow = WorkflowManager(config, self.state)
        self.opencode = OpenCodeClient(config)
        self._running = False
        self._tasks = []
    
    async def start(self):
        """Start the orchestrator."""
        logger.info("Starting Issue Orchestrator...")
        self._running = True
        
        # Load persisted state
        await self.state.load()
        
        # Start discovery loop
        discovery_task = asyncio.create_task(self._discovery_loop())
        self._tasks.append(discovery_task)
        
        # Start workflow processing loop
        workflow_task = asyncio.create_task(self._workflow_loop())
        self._tasks.append(workflow_task)
        
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
        
        logger.info("Issue Orchestrator stopped")
    
    async def _discovery_loop(self):
        """Periodically discover eligible issues."""
        while self._running:
            try:
                await self.discovery.discover_and_queue()
            except Exception as e:
                logger.error(f"Discovery error: {e}")
            
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )
    
    # Load configuration
    config_path = Path("/app/config.yaml")
    if not config_path.exists():
        logger.error(f"Config not found: {config_path}")
        sys.exit(1)
    
    with open(config_path) as f:
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
        while orchestrator._running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    asyncio.run(main())