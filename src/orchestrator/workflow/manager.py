"""
Workflow manager orchestration.

Owns the active-workflow processing loop, retry/backoff policy, and startup
recovery. Phase execution is delegated to orchestrator.workflow.phases.
"""

import logging
from datetime import UTC, datetime, timedelta

from git_provider import GitProvider, NotFoundError, ProviderError
from orchestrator.config import OrchestratorConfig
from orchestrator.opencode import OpenCodeClient, OpenCodeError, OpenCodeSessionManager
from orchestrator.providers import ProviderManager
from orchestrator.state import OrchestratorState, WorkflowPhase, WorkflowState
from orchestrator.workflow.phases import (
    PHASE_SPECS,
    WorkflowResult,
    execute_task_phase,
    phase_awaiting_review,
    phase_new,
    phase_stuck,
)

logger = logging.getLogger(__name__)


class WorkflowManager:
    """Manages workflow processing."""

    def __init__(self, config: OrchestratorConfig, state: OrchestratorState, providers: ProviderManager = None):
        self.config = config
        self.state = state
        self.opencode = OpenCodeClient(config)
        self.session_manager = OpenCodeSessionManager(self.opencode, state)
        self.providers = providers or ProviderManager(config)

    async def close(self):
        """Close owned HTTP clients."""
        await self.opencode.close()

    async def _get_provider(self, repository: str) -> GitProvider:
        """Get the provider that owns a repository."""
        return await self.providers.provider_for(repository)

    async def process_queue(self) -> int:
        """Process all active workflows."""
        active_workflows = self.state.list_active_workflows()
        processed = 0
        changed = False
        now = datetime.now(UTC)

        for workflow in active_workflows:
            if workflow.retry_at and workflow.retry_at > now:
                continue
            try:
                result = await self._process_workflow(workflow)
                if result.success:
                    processed += 1
                    workflow.retry_count = 0
                    workflow.last_error = ""
                    workflow.retry_at = None
                    self.state.set_workflow(workflow)
                    changed = True
                elif result.error:
                    logger.error(
                        f"Workflow {workflow.repository}#{workflow.issue_number} failed "
                        f"[{result.error_category or 'unknown'}]: {result.error}"
                    )
                    self._record_failure(workflow, result.error, result.error_category)
                    changed = True
                elif result.pending:
                    logger.info(f"Workflow {workflow.repository}#{workflow.issue_number} awaiting OpenCode task completion")
                    changed = True
            except Exception as e:
                logger.exception(f"Unexpected error processing workflow {workflow.repository}#{workflow.issue_number}")
                self._record_failure(workflow, str(e), "internal")
                changed = True

        if changed:
            await self.state.save()

        return processed

    def _record_failure(self, workflow: WorkflowState, error: str, category: str = "") -> None:
        """Record a phase failure and apply the retry/backoff policy."""
        workflow.retry_count += 1
        workflow.last_error = f"[{category or 'unknown'}] {error}"
        if workflow.retry_count >= self.config.retry.max_attempts:
            workflow.phase = WorkflowPhase.FAILED
            workflow.last_error = f"Exceeded {self.config.retry.max_attempts} attempts: [{category or 'unknown'}] {error}"
            workflow.retry_at = None
        else:
            delay = self._retry_delay(workflow.retry_count)
            workflow.retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        self.state.set_workflow(workflow)

    def _retry_delay(self, retry_count: int) -> int:
        """Exponential backoff delay for the next retry."""
        cfg = self.config.retry
        delay = cfg.base_delay_seconds * (cfg.exponential_base ** (retry_count - 1))
        return min(delay, cfg.max_delay_seconds)

    async def recover(self) -> dict:
        """Reconcile provider-side resources with persisted workflow state.

        Runs once at startup before resuming. For each workflow it checks the
        phase-appropriate issue label, the existence of the expected branch,
        and for completed workflows the existence of an open PR/MR. Returns a
        report of what was fixed or found missing; never raises.
        """
        report = {
            "checked": 0,
            "labels_fixed": 0,
            "missing_branches": [],
            "missing_pull_requests": [],
            "errors": [],
        }

        for workflow in self.state.list_workflows():
            key = f"{workflow.repository}#{workflow.issue_number}"
            report["checked"] += 1
            try:
                owner, repo = workflow.repository.split("/", 1)
                provider = await self._get_provider(workflow.repository)

                if workflow.branch and workflow.phase not in (WorkflowPhase.NEW, WorkflowPhase.FAILED):
                    try:
                        await provider.get_branch(owner, repo, workflow.branch)
                    except NotFoundError:
                        report["missing_branches"].append(key)

                if workflow.phase == WorkflowPhase.COMPLETED and workflow.branch:
                    prs = await provider.list_pull_requests(owner, repo, state="open", head=workflow.branch)
                    if not any(pr.head_branch == workflow.branch for pr in prs):
                        report["missing_pull_requests"].append(key)

                expected = self._expected_label(workflow.phase)
                if expected:
                    issue = await provider.get_issue(owner, repo, workflow.issue_number)
                    if expected not in issue.labels:
                        await provider.add_label(owner, repo, workflow.issue_number, expected)
                        report["labels_fixed"] += 1
                    for label in self._managed_labels():
                        if label != expected and label in issue.labels:
                            await provider.remove_label(owner, repo, workflow.issue_number, label)
                            report["labels_fixed"] += 1
            except Exception as e:  # noqa: BLE001 - recovery must not crash startup
                report["errors"].append(f"{key}: {e}")

        return report

    def _expected_label(self, phase: WorkflowPhase) -> str | None:
        """The issue label a workflow phase should carry, if any."""
        if phase == WorkflowPhase.AWAITING_REVIEW:
            return self.config.git_provider.labels.get("awaiting_review", "awaiting review")
        if phase in (
            WorkflowPhase.NEW,
            WorkflowPhase.PLANNING,
            WorkflowPhase.VERIFICATION_DESIGN,
            WorkflowPhase.WRITE_TESTS,
            WorkflowPhase.IMPLEMENTING,
            WorkflowPhase.VERIFY,
        ):
            return self.config.git_provider.labels.get("in_progress", "doing")
        return None

    def _managed_labels(self) -> list[str]:
        """Labels the orchestrator manages on issues."""
        labels = self.config.git_provider.labels
        return [
            labels.get("eligible", "agent"),
            labels.get("in_progress", "doing"),
            labels.get("stuck", "stuck"),
            labels.get("awaiting_review", "awaiting review"),
        ]

    async def _process_workflow(self, workflow: WorkflowState) -> WorkflowResult:
        """Process a single workflow based on its current phase."""
        logger.info(f"Processing workflow {workflow.repository}#{workflow.issue_number} in phase {workflow.phase.value}")
        try:
            if workflow.phase in PHASE_SPECS:
                return await execute_task_phase(self, workflow, PHASE_SPECS[workflow.phase])
            if workflow.phase == WorkflowPhase.NEW:
                return await phase_new(self, workflow)
            if workflow.phase == WorkflowPhase.AWAITING_REVIEW:
                return await phase_awaiting_review(self, workflow)
            if workflow.phase == WorkflowPhase.STUCK:
                return await phase_stuck(self, workflow)
            return WorkflowResult(success=False, error=f"Unknown phase: {workflow.phase}")
        except ProviderError as e:
            logger.error(f"Provider error in workflow {workflow.repository}#{workflow.issue_number}: {e}")
            return WorkflowResult(success=False, error=f"Provider error: {e}", error_category=e.category or "provider")
        except OpenCodeError as e:
            logger.error(f"OpenCode error in workflow {workflow.repository}#{workflow.issue_number}: {e}")
            return WorkflowResult(success=False, error=f"OpenCode error: {e}", error_category="opencode")
        except Exception as e:
            logger.exception(f"Unexpected error in workflow {workflow.repository}#{workflow.issue_number}")
            return WorkflowResult(success=False, error=str(e), error_category="internal")
