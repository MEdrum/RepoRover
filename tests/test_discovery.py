"""Tests for issue discovery."""

import asyncio
from unittest import mock

from git_provider import Issue, NotFoundError, ProviderType
from orchestrator.config import OrchestratorConfig
from orchestrator.discovery import IssueDiscovery
from orchestrator.state import OrchestratorState, WorkflowPhase, WorkflowState


def _issue(number=1, labels=None):
    return Issue(
        number=number,
        title="Fix bug",
        body="",
        state="open",
        labels=["agent"] if labels is None else labels,
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

    def __init__(self, issues=None, repos=()):
        self.issues = issues or []
        self.repos = set(repos)
        self.list_eligible_calls = []

    async def authenticate(self):
        return True

    async def get_repository(self, owner, repo):
        if f"{owner}/{repo}" in self.repos:
            return object()
        raise NotFoundError("not found")

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
        with mock.patch("git_provider.create_provider", return_value=fake):
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
        with mock.patch("git_provider.create_provider", return_value=fake):
            first = asyncio.run(discovery.discover_and_queue())
            second = asyncio.run(discovery.discover_and_queue())
        assert first == 1
        assert second == 0
        workflow = state.get_workflow("owner/repo", 1)
        assert workflow is not None


class TestDiscoveryWithBothProviders:
    def test_discovers_from_both_providers(self, tmp_path):
        config = OrchestratorConfig()
        config.git_provider.provider = "both"
        config.git_provider.repositories = ["ghorg/repo", "glorg/repo"]
        config.state.state_dir = str(tmp_path)
        state = OrchestratorState(str(tmp_path))
        discovery = IssueDiscovery(config, state)
        github = FakeProvider(issues=[_issue(1)], repos=["ghorg/repo"])
        gitlab = FakeProvider(issues=[_issue(2)], repos=["glorg/repo"])

        def _create(provider_type, cfg):
            if provider_type == ProviderType.GITHUB:
                return github
            return gitlab

        with mock.patch("git_provider.create_provider", side_effect=_create):
            queued = asyncio.run(discovery.discover_and_queue())
        assert queued == 2
        assert state.get_workflow("ghorg/repo", 1) is not None
        assert state.get_workflow("glorg/repo", 2) is not None
        assert github.list_eligible_calls[0][:2] == ("ghorg", "repo")
        assert gitlab.list_eligible_calls[0][:2] == ("glorg", "repo")


class StuckIssueProvider:
    """Fake provider whose get_issue returns scripted label sets."""

    def __init__(self, labels_by_issue):
        self.labels_by_issue = labels_by_issue

    async def authenticate(self):
        return True

    async def get_issue(self, owner, repo, number):
        return _issue(number=number, labels=self.labels_by_issue.get(number, []))


class TestRequeueStuckWorkflows:
    def test_resumes_when_stuck_label_removed(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        discovery = IssueDiscovery(_config(str(tmp_path)), state)
        provider = StuckIssueProvider(labels_by_issue={1: ["doing"]})
        workflow = WorkflowState(
            issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.STUCK, stuck_reason="waiting"
        )
        state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            requeued = asyncio.run(discovery.requeue_stuck_workflows())
        assert requeued == 1
        wf = state.get_workflow("owner/repo", 1)
        assert wf.phase == WorkflowPhase.PLANNING
        assert wf.stuck_reason == ""

    def test_keeps_waiting_when_stuck_label_present(self, tmp_path):
        state = OrchestratorState(str(tmp_path))
        discovery = IssueDiscovery(_config(str(tmp_path)), state)
        provider = StuckIssueProvider(labels_by_issue={2: ["stuck"]})
        workflow = WorkflowState(
            issue_number=2, repository="owner/repo", project="repo", phase=WorkflowPhase.STUCK, stuck_reason="waiting"
        )
        state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            requeued = asyncio.run(discovery.requeue_stuck_workflows())
        assert requeued == 0
        wf = state.get_workflow("owner/repo", 2)
        assert wf.phase == WorkflowPhase.STUCK
