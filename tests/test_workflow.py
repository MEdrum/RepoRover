"""Tests for workflow phase processing."""

import asyncio
from unittest import mock

from git_provider import Issue, Repository
from orchestrator.config import OrchestratorConfig
from orchestrator.state import OrchestratorState, WorkflowPhase, WorkflowState
from orchestrator.workflow import WorkflowManager


class FakeProvider:
    """Minimal fake Git provider for workflow phase tests."""

    def __init__(self, repository=None):
        self.repository = repository or Repository(
            id="1", name="repo", full_name="owner/repo", owner="owner",
            private=False, default_branch="develop", clone_url="",
            ssh_url="", html_url="", description=None,
        )
        self.branches = []
        self.comments = []

    async def authenticate(self):
        return True

    async def get_repository(self, owner, repo):
        return self.repository

    async def get_issue(self, owner, repo, number):
        return Issue(
            number=number, title="Fix bug", body="", state="open",
            labels=["agent"], assignees=[], author="tester", created_at="",
            updated_at="", html_url="", comments_count=0,
            repository="owner/repo",
        )

    async def add_label(self, owner, repo, number, label):
        return True

    async def remove_label(self, owner, repo, number, label):
        return True

    async def create_branch(self, owner, repo, branch, from_branch=None):
        self.branches.append((branch, from_branch))
        return mock.Mock()

    async def create_comment(self, owner, repo, number, body):
        self.comments.append(body)
        return mock.Mock()


class TestPhaseNewDefaultBranch:
    def test_branch_created_from_repository_default_branch(self, tmp_path):
        config = OrchestratorConfig()
        config.git_provider.repositories = ["owner/repo"]
        config.state.state_dir = str(tmp_path)
        state = OrchestratorState(str(tmp_path))
        manager = WorkflowManager(config, state)
        provider = FakeProvider()
        workflow = WorkflowState(issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.NEW)
        with mock.patch("git_provider.create_provider", return_value=provider):
            result = asyncio.run(manager._phase_new(workflow))
        assert result.success
        assert workflow.base_branch == "develop"
        assert ("agent/1-fix-bug", "develop") in provider.branches
        assert workflow.branch == "agent/1-fix-bug"
        assert workflow.phase == WorkflowPhase.PLANNING