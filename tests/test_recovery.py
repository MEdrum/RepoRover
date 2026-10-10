"""Tests for the restart recovery pass."""

import asyncio
from unittest import mock

from git_provider import Issue, NotFoundError, PullRequest
from orchestrator.config import OrchestratorConfig
from orchestrator.state import OrchestratorState, WorkflowPhase, WorkflowState
from orchestrator.workflow import WorkflowManager


def _issue(number, labels):
    return Issue(
        number=number, title="Fix bug", body="", state="open",
        labels=labels, assignees=[], author="tester", created_at="",
        updated_at="", html_url="", comments_count=0,
        repository="owner/repo",
    )


class FakeRecoveryProvider:
    def __init__(self, issues=None, branches=None, pull_requests=None):
        self.issues = issues or {}
        self.branches = branches or set()
        self.pull_requests = pull_requests or []
        self.added = []
        self.removed = []

    async def authenticate(self):
        return True

    async def get_issue(self, owner, repo, number):
        return self.issues[(owner, repo, number)]

    async def add_label(self, owner, repo, number, label):
        self.added.append((number, label))
        return True

    async def remove_label(self, owner, repo, number, label):
        self.removed.append((number, label))
        return True

    async def get_branch(self, owner, repo, branch):
        if branch in self.branches:
            return mock.Mock()
        raise NotFoundError(f"Branch {branch} not found")

    async def list_pull_requests(self, owner, repo, state="open", head=None, base=None):
        return [pr for pr in self.pull_requests if pr.head_branch == head]


def _manager(tmp_path):
    config = OrchestratorConfig()
    config.git_provider.repositories = ["owner/repo"]
    config.state.state_dir = str(tmp_path)
    state = OrchestratorState(str(tmp_path))
    return WorkflowManager(config, state)


class TestRecoverLabels:
    def test_adds_in_progress_and_removes_eligible(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeRecoveryProvider(
            issues={( "owner", "repo", 1): _issue(1, ["agent"])},
        )
        workflow = WorkflowState(issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.PLANNING, branch="agent/1-fix-bug")
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            report = asyncio.run(manager.recover())
        assert (1, "doing") in provider.added
        assert (1, "agent") in provider.removed
        assert report["labels_fixed"] >= 2

    def test_awaiting_review_gets_awaiting_review_label(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeRecoveryProvider(
            issues={("owner", "repo", 2): _issue(2, ["doing"])},
        )
        workflow = WorkflowState(issue_number=2, repository="owner/repo", project="repo", phase=WorkflowPhase.AWAITING_REVIEW, branch="agent/2-fix-bug")
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            report = asyncio.run(manager.recover())
        assert (2, "awaiting review") in provider.added
        assert (2, "doing") in provider.removed
        assert report["labels_fixed"] >= 2

    def test_stuck_workflow_labels_are_not_changed(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeRecoveryProvider(
            issues={("owner", "repo", 3): _issue(3, ["stuck"])},
        )
        workflow = WorkflowState(issue_number=3, repository="owner/repo", project="repo", phase=WorkflowPhase.STUCK)
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            report = asyncio.run(manager.recover())
        assert provider.added == []
        assert provider.removed == []
        assert report["labels_fixed"] == 0


class TestRecoverResources:
    def test_reports_missing_branch(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeRecoveryProvider(
            issues={("owner", "repo", 4): _issue(4, ["doing"])},
        )
        workflow = WorkflowState(issue_number=4, repository="owner/repo", project="repo", phase=WorkflowPhase.IMPLEMENTING, branch="agent/4-fix-bug")
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            report = asyncio.run(manager.recover())
        assert report["missing_branches"] == ["owner/repo#4"]

    def test_reports_missing_pr_for_completed(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeRecoveryProvider(
            issues={("owner", "repo", 5): _issue(5, [])},
        )
        workflow = WorkflowState(issue_number=5, repository="owner/repo", project="repo", phase=WorkflowPhase.COMPLETED, branch="agent/5-fix-bug")
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            report = asyncio.run(manager.recover())
        assert report["missing_pull_requests"] == ["owner/repo#5"]

    def test_existing_branch_and_pr_are_not_reported(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeRecoveryProvider(
            issues={("owner", "repo", 6): _issue(6, [])},
            branches={"agent/6-fix-bug"},
            pull_requests=[PullRequest(
                number=6, title="Fix: Fix bug", body="", state="open",
                head_branch="agent/6-fix-bug", base_branch="develop", author="tester",
                created_at="", updated_at="", html_url="https://example.com/pr/6", draft=False,
            )],
        )
        workflow = WorkflowState(issue_number=6, repository="owner/repo", project="repo", phase=WorkflowPhase.COMPLETED, branch="agent/6-fix-bug")
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            report = asyncio.run(manager.recover())
        assert report["missing_branches"] == []
        assert report["missing_pull_requests"] == []

    def test_provider_unavailable_is_reported(self, tmp_path):
        manager = _manager(tmp_path)
        workflow = WorkflowState(issue_number=7, repository="owner/repo", project="repo", phase=WorkflowPhase.NEW)
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", side_effect=RuntimeError("nope")):
            report = asyncio.run(manager.recover())
        assert report["errors"]
        assert report["checked"] == 0