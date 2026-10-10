"""Tests for workflow phase processing."""

import asyncio
from datetime import UTC, datetime
from unittest import mock

from git_provider import (
    Branch,
    Issue,
    NotFoundError,
    ProviderError,
    PullRequest,
    Repository,
)
from orchestrator.config import OrchestratorConfig
from orchestrator.opencode import OpenCodeResult
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


class FailingProvider:
    """Provider whose repository lookup always fails."""

    async def get_repository(self, owner, repo):
        raise ProviderError("boom")


class TestBoundedRetries:
    def test_phase_failure_increments_retry_and_schedules_backoff(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FailingProvider()
        workflow = WorkflowState(issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.NEW)
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            processed = asyncio.run(manager.process_queue())
        assert processed == 0
        wf = manager.state.get_workflow("owner/repo", 1)
        assert wf is not None
        assert wf.retry_count == 1
        assert wf.last_error
        assert wf.retry_at
        assert wf.retry_at > datetime.now(UTC).isoformat()

    def test_phase_failure_marks_workflow_failed_after_max_attempts(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FailingProvider()
        workflow = WorkflowState(issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.NEW, retry_count=2, retry_at="")
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            processed = asyncio.run(manager.process_queue())
        assert processed == 0
        wf = manager.state.get_workflow("owner/repo", 1)
        assert wf.retry_count == 3
        assert wf.phase == WorkflowPhase.FAILED
        assert "Exceeded" in wf.last_error
        assert wf.retry_at == ""

    def test_retry_delay_follows_exponential_backoff(self, tmp_path):
        manager = _manager(tmp_path)
        assert manager._retry_delay(1) == 2
        assert manager._retry_delay(2) == 4
        assert manager._retry_delay(6) == 60

    def test_phase_success_resets_retry_state(self, tmp_path):
        manager = _manager(tmp_path)
        workflow = WorkflowState(
            issue_number=1, repository="owner/repo", project="repo",
            phase=WorkflowPhase.STUCK, retry_count=2, last_error="boom", retry_at="",
        )
        manager.state.set_workflow(workflow)
        processed = asyncio.run(manager.process_queue())
        assert processed == 1
        wf = manager.state.get_workflow("owner/repo", 1)
        assert wf.retry_count == 0
        assert wf.last_error == ""
        assert wf.retry_at == ""


class FakeOpenCode:
    def __init__(self, status_results=None, run_results=None):
        self.status_results = list(status_results or [])
        self.run_results = list(run_results or [])
        self.run_calls = 0
        self.status_calls = 0

    async def run_task(self, task):
        self.run_calls += 1
        if self.run_results:
            return self.run_results.pop(0)
        return OpenCodeResult(success=True, output="plan")

    async def get_task_status(self, run_id):
        self.status_calls += 1
        if self.status_results:
            return self.status_results.pop(0)
        return None


class TestOpenCodeTaskPolling:
    def test_pending_task_persists_run_id_without_failure(self, tmp_path):
        manager = _manager(tmp_path)
        manager.opencode = FakeOpenCode(run_results=[OpenCodeResult(success=True, pending=True, run_id="abc")])
        provider = FakeProvider()
        workflow = WorkflowState(issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.PLANNING, branch="agent/1-fix-bug", base_branch="develop")
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            processed = asyncio.run(manager.process_queue())
        assert processed == 0
        wf = manager.state.get_workflow("owner/repo", 1)
        assert wf.opencode_run_id == "abc"
        assert wf.phase == WorkflowPhase.PLANNING
        assert wf.retry_count == 0

    def test_polls_stored_run_id_and_advances_on_completion(self, tmp_path):
        manager = _manager(tmp_path)
        manager.opencode = FakeOpenCode(status_results=[OpenCodeResult(success=True, output="A plan", run_id="abc")])
        provider = FakeProvider()
        workflow = WorkflowState(issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.PLANNING, branch="agent/1-fix-bug", base_branch="develop", opencode_run_id="abc")
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            result = asyncio.run(manager._phase_planning(workflow))
        assert result.success
        assert workflow.plan == "A plan"
        assert workflow.phase == WorkflowPhase.VERIFICATION_DESIGN
        assert workflow.opencode_run_id == ""

    def test_missing_status_reruns_task(self, tmp_path):
        manager = _manager(tmp_path)
        manager.opencode = FakeOpenCode(status_results=[None], run_results=[OpenCodeResult(success=True, output="B plan", run_id="xyz")])
        provider = FakeProvider()
        workflow = WorkflowState(issue_number=1, repository="owner/repo", project="repo", phase=WorkflowPhase.PLANNING, branch="agent/1-fix-bug", base_branch="develop", opencode_run_id="abc")
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            result = asyncio.run(manager._phase_planning(workflow))
        assert result.success
        assert workflow.plan == "B plan"
        assert workflow.opencode_run_id == ""
        assert manager.opencode.status_calls == 1
        assert manager.opencode.run_calls == 1
