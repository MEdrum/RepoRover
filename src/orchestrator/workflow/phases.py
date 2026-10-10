"""
Data-driven workflow phases.

The five OpenCode task phases are described by a PhaseSpec table and executed
by a single generic executor, removing the previous copy-paste. The special
phases (NEW, AWAITING_REVIEW, STUCK) are implemented as functions that take the
manager as a context object.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass

from git_provider import GitProvider, NotFoundError
from orchestrator.opencode import OpenCodeTask
from orchestrator.state import WorkflowPhase, WorkflowState
from orchestrator.workflow.prompts import (
    build_implementation_prompt,
    build_planning_prompt,
    build_verification_design_prompt,
    build_verification_prompt,
    build_write_tests_prompt,
    has_automated_tests,
    slugify,
)
from orchestrator.workflow.tasks import run_or_poll

logger = logging.getLogger(__name__)


@dataclass
class WorkflowResult:
    """Result of workflow processing."""
    success: bool
    next_phase: WorkflowPhase | None = None
    error: str = ""
    metadata: dict = None
    pending: bool = False


@dataclass(frozen=True)
class PhaseSpec:
    """Description of an OpenCode task phase."""
    phase: WorkflowPhase
    prompt_builder: Callable
    output_field: str | None
    next_phase: WorkflowPhase | Callable
    comment_prefix: str | None = None
    retry_phase: WorkflowPhase | None = None


def _verification_next(output: str) -> WorkflowPhase:
    return WorkflowPhase.WRITE_TESTS if has_automated_tests(output) else WorkflowPhase.IMPLEMENTING


PHASE_SPECS: dict[WorkflowPhase, PhaseSpec] = {
    WorkflowPhase.PLANNING: PhaseSpec(
        phase=WorkflowPhase.PLANNING,
        prompt_builder=build_planning_prompt,
        output_field="plan",
        next_phase=WorkflowPhase.VERIFICATION_DESIGN,
        comment_prefix="Agent plan",
    ),
    WorkflowPhase.VERIFICATION_DESIGN: PhaseSpec(
        phase=WorkflowPhase.VERIFICATION_DESIGN,
        prompt_builder=build_verification_design_prompt,
        output_field="verification_plan",
        next_phase=_verification_next,
        comment_prefix="Verification plan",
    ),
    WorkflowPhase.WRITE_TESTS: PhaseSpec(
        phase=WorkflowPhase.WRITE_TESTS,
        prompt_builder=build_write_tests_prompt,
        output_field=None,
        next_phase=WorkflowPhase.IMPLEMENTING,
    ),
    WorkflowPhase.IMPLEMENTING: PhaseSpec(
        phase=WorkflowPhase.IMPLEMENTING,
        prompt_builder=build_implementation_prompt,
        output_field=None,
        next_phase=WorkflowPhase.VERIFY,
    ),
    WorkflowPhase.VERIFY: PhaseSpec(
        phase=WorkflowPhase.VERIFY,
        prompt_builder=build_verification_prompt,
        output_field=None,
        next_phase=WorkflowPhase.AWAITING_REVIEW,
        comment_prefix="Verification complete",
        retry_phase=WorkflowPhase.IMPLEMENTING,
    ),
}


async def execute_task_phase(manager, workflow: WorkflowState, spec: PhaseSpec) -> WorkflowResult:
    """Run a single OpenCode task phase."""
    provider = await manager._get_provider(workflow.repository)
    owner, repo = workflow.repository.split("/", 1)
    issue = await provider.get_issue(owner, repo, workflow.issue_number)
    task = OpenCodeTask(
        repository=workflow.repository,
        project=workflow.project,
        branch=workflow.branch,
        issue_number=workflow.issue_number,
        issue_title=issue.title,
        issue_body=issue.body,
        issue_labels=issue.labels,
        phase=spec.phase.value,
        instructions=spec.prompt_builder(issue, workflow),
    )
    result = await run_or_poll(manager, workflow, task)
    if result.pending:
        manager.state.set_workflow(workflow)
        return WorkflowResult(success=False, pending=True)

    if not result.success:
        error = f"{spec.phase.value} failed: {result.error}"
        if spec.retry_phase is not None:
            workflow.phase = spec.retry_phase
            manager.state.set_workflow(workflow)
            return WorkflowResult(success=False, error=error, next_phase=spec.retry_phase)
        return WorkflowResult(success=False, error=error)

    if spec.output_field:
        setattr(workflow, spec.output_field, result.output)
    next_phase = spec.next_phase(result.output) if callable(spec.next_phase) else spec.next_phase
    workflow.phase = next_phase
    manager.state.set_workflow(workflow)

    if spec.comment_prefix:
        await provider.create_comment(
            owner, repo, workflow.issue_number,
            f"## {spec.comment_prefix}\n\n{result.output}",
        )
    return WorkflowResult(success=True, next_phase=next_phase)


async def ensure_branch(provider: GitProvider, owner: str, repo: str, branch: str, base: str) -> None:
    """Create a branch only if it does not already exist (idempotent)."""
    try:
        await provider.get_branch(owner, repo, branch)
    except NotFoundError:
        await provider.create_branch(owner, repo, branch, base)
    else:
        logger.info(f"Branch {branch} already exists, reusing")


async def ensure_pull_request(provider: GitProvider, owner: str, repo: str, title: str, body: str, head: str, base: str):
    """Find or create a PR/MR for the head branch (idempotent)."""
    existing = await provider.list_pull_requests(owner, repo, state="open", head=head)
    for pr in existing:
        if pr.head_branch == head and pr.base_branch == base:
            logger.info(f"PR/MR for {head} already exists, reusing")
            return pr
    return await provider.create_pull_request(
        owner=owner, repo=repo, title=title, body=body, head=head, base=base,
    )


async def phase_new(manager, workflow: WorkflowState) -> WorkflowResult:
    """Initialize a new workflow."""
    provider = await manager._get_provider(workflow.repository)
    in_progress_label = manager.config.git_provider.labels.get("in_progress", "doing")
    eligible_label = manager.config.git_provider.labels.get("eligible", "agent")
    try:
        owner, repo = workflow.repository.split("/", 1)
        repository = await provider.get_repository(owner, repo)
        workflow.base_branch = repository.default_branch or workflow.base_branch
        await provider.add_label(owner, repo, workflow.issue_number, in_progress_label)
        await provider.remove_label(owner, repo, workflow.issue_number, eligible_label)
        issue = await provider.get_issue(owner, repo, workflow.issue_number)
        branch_name = f"agent/{workflow.issue_number}-{slugify(issue.title)}"
        await ensure_branch(provider, owner, repo, branch_name, workflow.base_branch)
        workflow.branch = branch_name
        workflow.phase = WorkflowPhase.PLANNING
        manager.state.set_workflow(workflow)
        await provider.create_comment(
            owner, repo, workflow.issue_number,
            f"## Agent started\n\n**Branch:** `{branch_name}`\n**Base:** `{workflow.base_branch}`\n\nStarting planning phase...",
        )
        return WorkflowResult(success=True, next_phase=WorkflowPhase.PLANNING)
    except Exception as e:  # noqa: BLE001
        return WorkflowResult(success=False, error=f"Failed to initialize workflow: {e}")


async def phase_awaiting_review(manager, workflow: WorkflowState) -> WorkflowResult:
    """Create PR/MR and await human review."""
    provider = await manager._get_provider(workflow.repository)
    awaiting_review_label = manager.config.git_provider.labels.get("awaiting_review", "awaiting review")
    in_progress_label = manager.config.git_provider.labels.get("in_progress", "doing")
    try:
        owner, repo = workflow.repository.split("/", 1)
        issue = await provider.get_issue(owner, repo, workflow.issue_number)
        pr_title = f"Fix: {issue.title}"
        pr_body = f"Closes #{workflow.issue_number}\n\n## Summary\n\n{workflow.plan}\n\n## Verification\n\n{workflow.verification_plan}"
        pr = await ensure_pull_request(
            provider=provider, owner=owner, repo=repo, title=pr_title, body=pr_body,
            head=workflow.branch, base=workflow.base_branch,
        )
        await provider.add_label(owner, repo, workflow.issue_number, awaiting_review_label)
        await provider.remove_label(owner, repo, workflow.issue_number, in_progress_label)
        await provider.create_comment(
            owner, repo, workflow.issue_number,
            f"## Agent implementation complete\n\n**PR/MR:** {pr.html_url}\n\n**Branch:** `{workflow.branch}`\n\nAwaiting human review.",
        )
        workflow.phase = WorkflowPhase.COMPLETED
        manager.state.set_workflow(workflow)
        return WorkflowResult(success=True, next_phase=WorkflowPhase.COMPLETED)
    except Exception as e:  # noqa: BLE001
        return WorkflowResult(success=False, error=f"Failed to create PR/MR: {e}")


async def phase_stuck(manager, workflow: WorkflowState) -> WorkflowResult:
    """Stuck phase - wait for human response."""
    return WorkflowResult(success=True, next_phase=WorkflowPhase.STUCK)
