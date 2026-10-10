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
from orchestrator.opencode import OpenCodeError, OpenCodeResult
from orchestrator.state import OrchestratorState, WorkflowPhase, WorkflowState
from orchestrator.workflow import WorkflowManager
from orchestrator.workflow.phases import (
    PHASE_SPECS,
    execute_task_phase,
    phase_awaiting_review,
    phase_new,
)


class FakeProvider:
    """Minimal fake Git provider for workflow phase tests."""

    def __init__(self, repository=None, existing_branch=None, existing_pull_requests=None):
        self.repository = repository or Repository(
            id="1",
            name="repo",
            full_name="owner/repo",
            owner="owner",
            private=False,
            default_branch="develop",
            clone_url="",
            ssh_url="",
            html_url="",
            description=None,
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
            if (head is None or pr.head_branch == head) and (base is None or pr.base_branch == base):
                result.append(pr)
        return result

    async def create_pull_request(self, owner, repo, title, body, head, base, draft=False):
        self.created_pull_requests.append((head, base))
        return PullRequest(
            number=len(self.created_pull_requests),
            title=title,
            body=body,
            state="open",
            head_branch=head,
            base_branch=base,
            author="tester",
            created_at="",
            updated_at="",
            html_url=f"https://example.com/pr/{len(self.created_pull_requests)}",
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
            result = asyncio.run(phase_new(manager, workflow))
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
            result = asyncio.run(phase_new(manager, workflow))
        assert result.success
        assert workflow.branch == "agent/1-fix-bug"
        assert workflow.base_branch == "develop"
        assert provider.branches == []  # create_branch was not called

    def test_phase_awaiting_review_reuses_existing_pr(self, tmp_path):
        manager = _manager(tmp_path)
        existing_pr = PullRequest(
            number=9,
            title="Fix: Fix bug",
            body="",
            state="open",
            head_branch="agent/1-fix-bug",
            base_branch="develop",
            author="tester",
            created_at="",
            updated_at="",
            html_url="https://example.com/pr/9",
            draft=False,
        )
        provider = FakeProvider(existing_pull_requests=[existing_pr])
        workflow = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.AWAITING_REVIEW,
            branch="agent/1-fix-bug",
            base_branch="develop",
            plan="A plan",
            verification_plan="A verification plan",
        )
        with mock.patch("git_provider.create_provider", return_value=provider):
            result = asyncio.run(phase_awaiting_review(manager, workflow))
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
        assert wf.retry_at > datetime.now(UTC)

    def test_phase_failure_marks_workflow_failed_after_max_attempts(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FailingProvider()
        workflow = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.NEW,
            retry_count=2,
            retry_at=None,
        )
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            processed = asyncio.run(manager.process_queue())
        assert processed == 0
        wf = manager.state.get_workflow("owner/repo", 1)
        assert wf.retry_count == 3
        assert wf.phase == WorkflowPhase.FAILED
        assert "Exceeded" in wf.last_error
        assert wf.retry_at is None

    def test_retry_delay_follows_exponential_backoff(self, tmp_path):
        manager = _manager(tmp_path)
        assert manager._retry_delay(1) == 2
        assert manager._retry_delay(2) == 4
        assert manager._retry_delay(6) == 60

    def test_phase_success_resets_retry_state(self, tmp_path):
        manager = _manager(tmp_path)
        workflow = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.STUCK,
            retry_count=2,
            last_error="boom",
            retry_at=None,
        )
        manager.state.set_workflow(workflow)
        processed = asyncio.run(manager.process_queue())
        assert processed == 1
        wf = manager.state.get_workflow("owner/repo", 1)
        assert wf.retry_count == 0
        assert wf.last_error == ""
        assert wf.retry_at is None


class TestOpenCodeSessionPhases:
    def test_pending_result_keeps_workflow_in_phase_without_failure(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeProvider()
        workflow = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.PLANNING,
            branch="agent/1-fix-bug",
            base_branch="develop",
        )
        manager.state.set_workflow(workflow)
        with (
            mock.patch.object(
                manager.session_manager,
                "run_or_poll",
                new_callable=mock.AsyncMock,
                return_value=OpenCodeResult(success=True, pending=True, run_id="ses_1"),
            ),
            mock.patch("git_provider.create_provider", return_value=provider),
        ):
            processed = asyncio.run(manager.process_queue())
        assert processed == 0
        wf = manager.state.get_workflow("owner/repo", 1)
        assert wf.phase == WorkflowPhase.PLANNING
        assert wf.retry_count == 0

    def test_success_result_advances_phase_with_output(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeProvider()
        workflow = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.PLANNING,
            branch="agent/1-fix-bug",
            base_branch="develop",
            opencode_session_id="ses_1",
        )
        manager.state.set_workflow(workflow)
        with (
            mock.patch.object(
                manager.session_manager,
                "run_or_poll",
                new_callable=mock.AsyncMock,
                return_value=OpenCodeResult(success=True, output="A plan", run_id="ses_1"),
            ),
            mock.patch("git_provider.create_provider", return_value=provider),
        ):
            result = asyncio.run(execute_task_phase(manager, workflow, PHASE_SPECS[WorkflowPhase.PLANNING]))
        assert result.success
        assert workflow.plan == "A plan"
        assert workflow.phase == WorkflowPhase.VERIFICATION_DESIGN
        assert workflow.opencode_session_id == "ses_1"  # session id persists across phases

    def test_failed_result_returns_failure_without_advancing(self, tmp_path):
        manager = _manager(tmp_path)
        provider = FakeProvider()
        workflow = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.PLANNING,
            branch="agent/1-fix-bug",
            base_branch="develop",
            opencode_session_id="ses_1",
        )
        manager.state.set_workflow(workflow)
        with (
            mock.patch.object(
                manager.session_manager,
                "run_or_poll",
                new_callable=mock.AsyncMock,
                return_value=OpenCodeResult(success=False, error="boom", run_id="ses_1"),
            ),
            mock.patch("git_provider.create_provider", return_value=provider),
        ):
            result = asyncio.run(execute_task_phase(manager, workflow, PHASE_SPECS[WorkflowPhase.PLANNING]))
        assert not result.success
        assert "failed" in result.error
        assert workflow.phase == WorkflowPhase.PLANNING
        assert workflow.opencode_session_id == "ses_1"


class TestShutdown:
    def test_close_closes_opencode_client(self, tmp_path):
        manager = _manager(tmp_path)
        with mock.patch.object(manager.opencode, "close", new_callable=mock.AsyncMock) as close_mock:
            asyncio.run(manager.close())
        assert close_mock.await_count == 1


class RaisingProvider:
    """Provider whose get_issue always raises a given exception."""

    def __init__(self, exc):
        self.exc = exc

    async def authenticate(self):
        return True

    async def get_issue(self, owner, repo, number):
        raise self.exc


class TestErrorCategorization:
    def test_provider_error_is_categorized(self, tmp_path):
        manager = _manager(tmp_path)
        provider = RaisingProvider(ProviderError("rate limited", category="rate_limited"))
        workflow = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.PLANNING,
            branch="agent/1-fix-bug",
        )
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            result = asyncio.run(manager._process_workflow(workflow))
        assert not result.success
        assert result.error_category == "rate_limited"

    def test_opencode_error_is_categorized(self, tmp_path):
        manager = _manager(tmp_path)
        provider = RaisingProvider(OpenCodeError("boom"))
        workflow = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.PLANNING,
            branch="agent/1-fix-bug",
        )
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            result = asyncio.run(manager._process_workflow(workflow))
        assert not result.success
        assert result.error_category == "opencode"

    def test_internal_error_is_categorized(self, tmp_path):
        manager = _manager(tmp_path)
        provider = RaisingProvider(RuntimeError("bug"))
        workflow = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.PLANNING,
            branch="agent/1-fix-bug",
        )
        manager.state.set_workflow(workflow)
        with mock.patch("git_provider.create_provider", return_value=provider):
            result = asyncio.run(manager._process_workflow(workflow))
        assert not result.success
        assert result.error_category == "internal"

    def test_failing_workflow_does_not_block_others(self, tmp_path):
        manager = _manager(tmp_path)
        bad_provider = RaisingProvider(RuntimeError("bug"))
        bad = WorkflowState(
            issue_number=1,
            repository="owner/repo",
            project="repo",
            phase=WorkflowPhase.PLANNING,
            branch="agent/1-fix-bug",
        )
        good = WorkflowState(issue_number=2, repository="owner/repo", project="repo", phase=WorkflowPhase.STUCK)
        manager.state.set_workflow(bad)
        manager.state.set_workflow(good)
        with mock.patch("git_provider.create_provider", return_value=bad_provider):
            processed = asyncio.run(manager.process_queue())
        assert processed == 1
        bad_wf = manager.state.get_workflow("owner/repo", 1)
        assert "[internal]" in bad_wf.last_error
