"""Tests for the orchestrator lifecycle and wiring."""

from orchestrator.config import OrchestratorConfig
from orchestrator.main import IssueOrchestrator


class TestIssueOrchestratorWiring:
    def test_discovery_shares_orchestrator_state(self, tmp_path):
        config = OrchestratorConfig.from_dict({
            "git_provider": {"repositories": ["owner/repo"]},
            "state": {"state_dir": str(tmp_path)},
        })
        orchestrator = IssueOrchestrator(config)
        assert orchestrator.discovery.state is orchestrator.state