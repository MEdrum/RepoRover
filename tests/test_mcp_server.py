"""Tests for the RepoRover MCP server wrapper."""

import asyncio
from unittest import mock

from orchestrator.config import OrchestratorConfig
from orchestrator.mcp_server import RepoRoverMCPServer
from orchestrator.state import OrchestratorState, WorkflowPhase
from orchestrator.workflow.manager import WorkflowManager


class TestMCPServer:
    def _manager(self, tmp_path):
        config = OrchestratorConfig()
        config.state.state_dir = str(tmp_path)
        state = OrchestratorState(str(tmp_path))
        return WorkflowManager(config, state)

    def test_registers_both_tools(self, tmp_path):
        server = RepoRoverMCPServer(self._manager(tmp_path))
        tools = asyncio.run(server.server.list_tools())
        names = {getattr(t, "name", None) or (t[0] if isinstance(t, tuple) else None) for t in tools}
        assert "report_done" in names
        assert "report_stuck" in names

    def test_report_done_forwards_to_manager(self, tmp_path):
        manager = self._manager(tmp_path)
        manager.report_done = mock.AsyncMock(
            return_value=mock.Mock(success=True, next_phase=WorkflowPhase.VERIFICATION_DESIGN)
        )
        server = RepoRoverMCPServer(manager)
        out = asyncio.run(server.report_done("owner/repo", 1, "plan"))
        assert "advanced" in out
        manager.report_done.assert_awaited_once_with("owner/repo", 1, "plan")

    def test_report_done_failure_is_reported(self, tmp_path):
        manager = self._manager(tmp_path)
        manager.report_done = mock.AsyncMock(return_value=mock.Mock(success=False, next_phase=None, error="boom"))
        server = RepoRoverMCPServer(manager)
        out = asyncio.run(server.report_done("owner/repo", 1, "plan"))
        assert "failed" in out

    def test_report_stuck_forwards_to_manager(self, tmp_path):
        manager = self._manager(tmp_path)
        manager.report_stuck = mock.AsyncMock(return_value=mock.Mock(success=True, next_phase=WorkflowPhase.STUCK))
        server = RepoRoverMCPServer(manager)
        out = asyncio.run(server.report_stuck("owner/repo", 1, "need a decision"))
        assert "stuck" in out
        manager.report_stuck.assert_awaited_once_with("owner/repo", 1, "need a decision")
