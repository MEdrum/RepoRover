"""Tests for the orchestrator lifecycle and wiring."""

import asyncio
from unittest import mock

from orchestrator.config import OrchestratorConfig
from orchestrator.main import IssueOrchestrator


class TestIssueOrchestratorWiring:
    def test_discovery_shares_orchestrator_state(self, tmp_path):
        config = OrchestratorConfig.from_dict({
            "git_provider": {"repositories": ["owner/repo"], "github": {"token": "t"}},
            "state": {"state_dir": str(tmp_path)},
        })
        orchestrator = IssueOrchestrator(config)
        assert orchestrator.discovery.state is orchestrator.state

    def test_discovery_loop_uses_configured_polling_interval(self, tmp_path):
        config = OrchestratorConfig.from_dict({
            "git_provider": {
                "repositories": ["owner/repo"],
                "github": {"token": "t"},
                "polling": {"interval_seconds": 123},
            },
            "state": {"state_dir": str(tmp_path)},
        })
        orchestrator = IssueOrchestrator(config)
        orchestrator._running = True
        assert orchestrator.config.git_provider.polling_interval == 123

        def stop_after_one_poll(*args, **kwargs):
            orchestrator._running = False
            raise RuntimeError("stop")

        with mock.patch.object(
            orchestrator.discovery, "discover_and_queue", side_effect=stop_after_one_poll
        ), mock.patch(
            "orchestrator.main.asyncio.sleep", new_callable=mock.AsyncMock
        ) as sleep_mock:

            asyncio.run(orchestrator._discovery_loop())
        assert sleep_mock.await_count == 1
        assert sleep_mock.await_args == mock.call(123)


class TestIssueOrchestratorShutdown:
    def test_stop_closes_opencode_clients(self, tmp_path):
        config = OrchestratorConfig.from_dict({
            "git_provider": {"repositories": ["owner/repo"], "github": {"token": "t"}},
            "state": {"state_dir": str(tmp_path)},
        })
        orchestrator = IssueOrchestrator(config)
        with mock.patch.object(orchestrator.opencode, "close", new_callable=mock.AsyncMock) as oc_close, mock.patch.object(
            orchestrator.workflow, "close", new_callable=mock.AsyncMock
        ) as wf_close:
            asyncio.run(orchestrator.stop())
        assert oc_close.await_count == 1
        assert wf_close.await_count == 1
