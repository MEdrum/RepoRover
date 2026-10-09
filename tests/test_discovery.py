"""Tests for issue discovery."""

import asyncio
from unittest import mock

from git_provider import Issue
from orchestrator.config import OrchestratorConfig
from orchestrator.discovery import IssueDiscovery
from orchestrator.state import OrchestratorState, WorkflowPhase


def _issue(number=1):
    return Issue(
        number=number,
        title="Fix bug",
        body="",
        state="open",
        labels=["agent"],
        assignees=[],
        author="tester",
        created_at="",
        updated_at="",
        html_url="",
        comments_count=0,
        repository="owner/repo",
    )


class FakeProvider:
    """Minimal fake Git provider for discovery tests."""

    def __init__(self, issues=None):
        self.issues = issues or []
        self.list_eligible_calls = []

    async def authenticate(self):
        return True

    async def list_eligible_issues(self, owner, repo, label="agent", limit=100):
        self.list_eligible_calls.append((owner, repo, label, limit))
        return self.issues


def _config(state_dir):
    config = OrchestratorConfig()
    config.git_provider.repositories = ["owner/repo"]
    config.state.state_dir = state_dir
    return config


class TestIssueDiscoveryStateSharing:
    def test_queues_discovered_issue_into_shared_state(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        discovery = IssueDiscovery(_config(str(tmp_path)), state)
        fake = FakeProvider(issues=[_issue()])
        with mock.patch("orchestrator.discovery.create_provider", return_value=fake):
            queued = asyncio.run(discovery.discover_and_queue())
        assert queued == 1
        workflow = state.get_workflow("owner/repo", 1)
        assert workflow is not None
        assert workflow.repository == "owner/repo"
        assert workflow.phase == WorkflowPhase.NEW

    def test_existing_workflow_is_not_requeued(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        discovery = IssueDiscovery(_config(str(tmp_path)), state)
        fake = FakeProvider(issues=[_issue()])
        with mock.patch("orchestrator.discovery.create_provider", return_value=fake):
            first = asyncio.run(discovery.discover_and_queue())
            second = asyncio.run(discovery.discover_and_queue())
        assert first == 1
        assert second == 0
        workflow = state.get_workflow("owner/repo", 1)
        assert workflow is not None