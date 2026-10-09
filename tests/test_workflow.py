"""Tests for workflow phase processing."""

import asyncio
from unittest import mock

from git_provider import Branch, Issue, NotFoundError, PullRequest, Repository
from orchestrator.config import OrchestratorConfig
from orchestrator.state import OrchestratorState, WorkflowPhase, WorkflowState
from orchestrator.workflow import WorkflowManager


class FakeProvider:
    """Minimal fake Git provider for workflow phase tests."""

    def __init__(self, repository=None, existing_branch=None, existing_pull_requests=None):
        self.repository = repository or Repository(
            id="1", name="repo", full_name="owner/repo", owner="owner",
            private=False, default_branch="develop", clone_url="",
            ssh_url="", html_url="", description=None,
        )
        self.existing_branch = existing_branch
        self.existing_pull_requests = existing_pull_requests or []
        self.branches = []
        self.created_pull_requests = []
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

    async def get_branch(self, owner, repo, branch):
        if self.existing_branch is not None:
            return self.existing_branch
        raise NotFoundError(f"Branch {branch} not found")

    async def add_label(self, owner, repo, number, label):
        return True

    async def remove_label(self, owner, repo, number, label):
        return True

    async def create_branch(self, owner, repo, branch, from_branch=None):
        self.branches.append((branch, from_branch))
        return Branch(name=branch, commit_sha="abc", protected=False)


    async def create_comment(self, owner, repo, number, body):
        self.comments.append(body)
        return mock.Mock()

    async def list_pull_requests(self, owner, repo, state="open", head=None, base=None):
        result = []
        for pr in self.existing_pull_requests:

            if (head is None or pr.head_branch == head)and (base is None or pr.base_branch == base):
                result.append(pr)
        return result

    async def create_pull_request(self, owner, repo, title, body, head, base, draft=False):
        self.created_pull_requests.append((head, base))
        return PullRequest(
            number=len(self.created_pull_requests),
            title=title, body=body, state="open",
            head_branch=head, base_branch=base, author="tester",
            created_at="", updated_at="", html_url=f"https://example.com/pr/{len(self.created_pull_requests)}",
            draft=draft,
        )


def _manager(tmp_path):
    config = OrchestratorConfig()
    config.git_provider.repositories = ["owner/repo"]
    config.state.state_dir = str(tmp_path)

    state = OrchestratorState(str(tmp_path))
    return WorkflowManager(config, state)


class TestPhaseNewDefaultBranch:
    def test_branch_created_from_repository_default_branch(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeProvider()
        workflow = WorkflowState(issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.NEW)
        with mock.patch("git_provider.create_provider", return_value=provider):
            result = asyncio.run(manager._phase_new(workflow))
        assert result.success
        assert workflow.base_branch == "develop"
        assert ("agent/1-fix-bug", "develop") in provider.branches
        assert workflow.branch == "agent/1-fix-bug"
        assert workflow.phase == WorkflowPhase.PLANNING


class TestIdempotentSideEffects:



    def test_phase_new_reuses_existing_branch(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeProvider(existing_branch=Branch(name="agent/1-fix-bug", commit_sha="abc", protected=False))
        workflow = WorkflowState(issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.NEW)

        with mock.patch("git_provider.create_provider", return_value=provider):

            result = asyncio.run(manager._phase_new(workflow))
        assert result.success
        assert workflow.branch == "agent/1-fix-bug"
        assert workflow.base_branch == "develop"
        assert provider.branches == []  # create_branch was not called


    def test_phase_awaiting_review_reuses_existing_pr(self, tmp_path):
        manager = _manager(tmp_path)
        existing_pr = PullRequest(
            number=9, title="Fix: Fix bug", body="", state="open",
            head_branch="agent/1-fix-bug", base_branch="develop", author="tester",
            created_at="", updated_at="", html_url="https://example.com/pr/9", draft=False,
        )
        provider = FakeProvider(existing_pull_requests=[existing_pr])
        workflow = WorkflowState(
            issue_number=1, repository="owner/repo", project="repo",
            phase=WorkflowPhase.AWAITING_REVIEW, branch="agent/1-fix-bug",
            base_branch="develop", plan="A plan", verification_plan="A verification plan",
        )
        with mock.patch("git_provider.create_provider", return_value=provider):

            result = asyncio.run(manager._phase_awaiting_review(workflow))
        assert result.success
        assert workflow.phase == WorkflowPhase.COMPLETED
        assert provider.created_pull_requests == []  # create_pull_request was not called
