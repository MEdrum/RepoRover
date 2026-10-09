"""
Workflow Manager

Manages the workflow state machine for issue processing.
"""

import logging
from typing import Optional, List
from dataclasses import dataclass

from orchestrator.config import OrchestratorConfig
from orchestrator.state import OrchestratorState, WorkflowState, WorkflowPhase
from orchestrator.opencode import OpenCodeClient, OpenCodeTask
from git_provider import GitProvider, ProviderError, NotFoundError

logger = logging.getLogger(__name__)


@dataclass
class WorkflowResult:
    """Result of workflow processing."""
    success: bool
    next_phase: Optional[WorkflowPhase] = None
    error: str = ""
    metadata: dict = None


class WorkflowManager:
    """Manages workflow processing."""
    
    def __init__(self, config: OrchestratorConfig, state: OrchestratorState):
        self.config = config
        self.state = state
        self.opencode = OpenCodeClient(config)
        self._provider: Optional[GitProvider] = None
    
    async def _get_provider(self) -> GitProvider:
        """Get or create Git provider."""
        if self._provider is None:
            from git_provider import create_provider, ProviderType
            provider_type = ProviderType.GITHUB if self.config.git_provider.provider in ("github", "both") else ProviderType.GITLAB
            provider_config = {}
            
            if provider_type == ProviderType.GITHUB:
                provider_config = {
                    "token": self.config.git_provider.github_token,
                    "base_url": self.config.git_provider.github_base_url
                }
            else:
                provider_config = {
                    "token": self.config.git_provider.gitlab_token,
                    "url": self.config.git_provider.gitlab_url
                }
            
            self._provider = create_provider(provider_type, provider_config)
            await self._provider.authenticate()
        
        return self._provider
    
    async def process_queue(self) -> int:
        """Process all active workflows."""
        active_workflows = self.state.list_active_workflows()
        processed = 0
        
        for workflow in active_workflows:
            try:
                result = await self._process_workflow(workflow)
                if result.success:
                    processed += 1
                elif result.error:
                    logger.error(f"Workflow {workflow.repository}#{workflow.issue_number} failed: {result.error}")
            except Exception as e:
                logger.error(f"Error processing workflow {workflow.repository}#{workflow.issue_number}: {e}")
                workflow.phase = WorkflowPhase.FAILED
                workflow.last_error = str(e)
                self.state.set_workflow(workflow)
        
        if processed > 0:
            await self.state.save()
        
        return processed
    
    async def _process_workflow(self, workflow: WorkflowState) -> WorkflowResult:
        """Process a single workflow based on its current phase."""
        logger.info(f"Processing workflow {workflow.repository}#{workflow.issue_number} in phase {workflow.phase.value}")
        
        try:
            if workflow.phase == WorkflowPhase.NEW:
                return await self._phase_new(workflow)
            elif workflow.phase == WorkflowPhase.PLANNING:
                return await self._phase_planning(workflow)
            elif workflow.phase == WorkflowPhase.VERIFICATION_DESIGN:
                return await self._phase_verification_design(workflow)
            elif workflow.phase == WorkflowPhase.WRITE_TESTS:
                return await self._phase_write_tests(workflow)
            elif workflow.phase == WorkflowPhase.IMPLEMENTING:
                return await self._phase_implementing(workflow)
            elif workflow.phase == WorkflowPhase.VERIFY:
                return await self._phase_verify(workflow)
            elif workflow.phase == WorkflowPhase.AWAITING_REVIEW:
                return await self._phase_awaiting_review(workflow)
            elif workflow.phase == WorkflowPhase.STUCK:
                return await self._phase_stuck(workflow)
            else:
                return WorkflowResult(success=False, error=f"Unknown phase: {workflow.phase}")
        except ProviderError as e:
            logger.error(f"Provider error in workflow {workflow.repository}#{workflow.issue_number}: {e}")
            return WorkflowResult(success=False, error=f"Provider error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error in workflow {workflow.repository}#{workflow.issue_number}: {e}")
            return WorkflowResult(success=False, error=str(e))
    
    async def _ensure_branch(self, provider: GitProvider, owner: str, repo: str, branch: str, base: str) -> None:
        """Create a branch only if it does not already exist (idempotent."""
        try:
            await provider.get_branch(owner, repo, branch)
        except NotFoundError:
            await provider.create_branch(owner, repo, branch, base)
        else:
            logger.info(f"Branch {branch} already exists, reusing")

    async def _ensure_pull_request(self, provider: GitProvider, owner: str, repo: str, title: str, body: str, head: str, base: str):
        """Find or create a PR/MR for the head branch (idempotent."""
        existing = await provider.list_pull_requests(owner, repo, state="open", head=head)
        for pr in existing:
            if pr.head_branch == head and pr.base_branch == base:
                logger.info(f"PR/MR for {head} already exists, reusing")
                return pr
        return await provider.create_pull_request(
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            head=head,
            base=base
        )

    async def _phase_new(self, workflow: WorkflowState) -> WorkflowResult:
        """Initialize new workflow."""
        provider = await self._get_provider()
        in_progress_label = self.config.git_provider.labels.get("in_progress", "doing")
        eligible_label = self.config.git_provider.labels.get("eligible", "agent")
        
        try:
            owner, repo = workflow.repository.split("/", 1)
            
            # Resolve the repository's actual default branch
            repository = await provider.get_repository(owner, repo)
            workflow.base_branch = repository.default_branch or workflow.base_branch
            
            # Add in_progress label, remove eligible label
            await provider.add_label(owner, repo, workflow.issue_number, in_progress_label)
            await provider.remove_label(owner, repo, workflow.issue_number, eligible_label)
            
            # Get issue details
            issue = await provider.get_issue(owner, repo, workflow.issue_number)
            
            # Create feature branch (idempotently; reuse an existing branch on retry)
            branch_name = f"agent/{workflow.issue_number}-{self._slugify(issue.title)}"
            await self._ensure_branch(provider, owner, repo, branch_name, workflow.base_branch)
            
            workflow.branch = branch_name
            workflow.phase = WorkflowPhase.PLANNING
            self.state.set_workflow(workflow)
            
            # Comment on issue
            await provider.create_comment(
                owner, repo, workflow.issue_number,
                f"## Agent started\n\n**Branch:** `{branch_name}`\n**Base:** `{workflow.base_branch}`\n\nStarting planning phase..."
            )
            
            return WorkflowResult(success=True, next_phase=WorkflowPhase.PLANNING)
            
        except Exception as e:
            return WorkflowResult(success=False, error=f"Failed to initialize workflow: {e}")
    
    async def _phase_planning(self, workflow: WorkflowState) -> WorkflowResult:
        """Planning phase - analyze issue and create plan."""
        provider = await self._get_provider()
        
        try:
            owner, repo = workflow.repository.split("/", 1)
            issue = await provider.get_issue(owner, repo, workflow.issue_number)
            
            # Create planning task for OpenCode
            task = OpenCodeTask(
                repository=workflow.repository,
                project=workflow.project,
                branch=workflow.branch,
                issue_number=workflow.issue_number,
                issue_title=issue.title,
                issue_body=issue.body,
                issue_labels=issue.labels,
                phase="planning",
                instructions=self._build_planning_prompt(issue, workflow)
            )
            
            # Invoke OpenCode
            result = await self.opencode.run_task(task)
            
            if result.success:
                workflow.plan = result.output
                workflow.phase = WorkflowPhase.VERIFICATION_DESIGN
                self.state.set_workflow(workflow)
                
                # Post plan as comment
                await provider.create_comment(
                    owner, repo, workflow.issue_number,
                    f"## Agent plan\n\n{result.output}"
                )
                
                return WorkflowResult(success=True, next_phase=WorkflowPhase.VERIFICATION_DESIGN)
            else:
                return WorkflowResult(success=False, error=f"Planning failed: {result.error}")
                
        except Exception as e:
            return WorkflowResult(success=False, error=f"Planning phase failed: {e}")
    
    async def _phase_verification_design(self, workflow: WorkflowState) -> WorkflowResult:
        """Verification design phase - determine verification strategy."""
        provider = await self._get_provider()
        
        try:
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
                phase="verification_design",
                instructions=self._build_verification_design_prompt(issue, workflow)
            )
            
            result = await self.opencode.run_task(task)
            
            if result.success:
                workflow.verification_plan = result.output
                
                # Determine next phase based on verification plan
                if self._has_automated_tests(result.output):
                    workflow.phase = WorkflowPhase.WRITE_TESTS
                else:
                    workflow.phase = WorkflowPhase.IMPLEMENTING
                
                self.state.set_workflow(workflow)
                
                # Post verification plan
                await provider.create_comment(
                    owner, repo, workflow.issue_number,
                    f"## Verification plan\n\n{result.output}"
                )
                
                return WorkflowResult(success=True, next_phase=workflow.phase)
            else:
                return WorkflowResult(success=False, error=f"Verification design failed: {result.error}")
                
        except Exception as e:
            return WorkflowResult(success=False, error=f"Verification design failed: {e}")
    
    async def _phase_write_tests(self, workflow: WorkflowState) -> WorkflowResult:
        """Write tests phase."""
        provider = await self._get_provider()
        
        try:
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
                phase="write_tests",
                instructions=self._build_write_tests_prompt(issue, workflow)
            )
            
            result = await self.opencode.run_task(task)
            
            if result.success:
                workflow.phase = WorkflowPhase.IMPLEMENTING
                self.state.set_workflow(workflow)
                return WorkflowResult(success=True, next_phase=WorkflowPhase.IMPLEMENTING)
            else:
                return WorkflowResult(success=False, error=f"Test writing failed: {result.error}")
                
        except Exception as e:
            return WorkflowResult(success=False, error=f"Write tests phase failed: {e}")
    
    async def _phase_implementing(self, workflow: WorkflowState) -> WorkflowResult:
        """Implementation phase."""
        provider = await self._get_provider()
        
        try:
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
                phase="implementing",
                instructions=self._build_implementation_prompt(issue, workflow)
            )
            
            result = await self.opencode.run_task(task)
            
            if result.success:
                workflow.phase = WorkflowPhase.VERIFY
                self.state.set_workflow(workflow)
                return WorkflowResult(success=True, next_phase=WorkflowPhase.VERIFY)
            else:
                return WorkflowResult(success=False, error=f"Implementation failed: {result.error}")
                
        except Exception as e:
            return WorkflowResult(success=False, error=f"Implementation phase failed: {e}")
    
    async def _phase_verify(self, workflow: WorkflowState) -> WorkflowResult:
        """Verification phase."""
        provider = await self._get_provider()
        
        try:
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
                phase="verify",
                instructions=self._build_verification_prompt(issue, workflow)
            )
            
            result = await self.opencode.run_task(task)
            
            if result.success:
                workflow.phase = WorkflowPhase.AWAITING_REVIEW
                self.state.set_workflow(workflow)
                
                # Post verification results
                await provider.create_comment(
                    owner, repo, workflow.issue_number,
                    f"## Verification complete\n\n{result.output}"
                )
                
                return WorkflowResult(success=True, next_phase=WorkflowPhase.AWAITING_REVIEW)
            else:
                # Verification failed, go back to implementing
                workflow.phase = WorkflowPhase.IMPLEMENTING
                self.state.set_workflow(workflow)
                return WorkflowResult(success=False, error=f"Verification failed: {result.error}", next_phase=WorkflowPhase.IMPLEMENTING)
                
        except Exception as e:
            return WorkflowResult(success=False, error=f"Verification phase failed: {e}")
    
    async def _phase_awaiting_review(self, workflow: WorkflowState) -> WorkflowResult:
        """Create PR/MR and await human review."""
        provider = await self._get_provider()
        awaiting_review_label = self.config.git_provider.labels.get("awaiting_review", "awaiting review")
        in_progress_label = self.config.git_provider.labels.get("in_progress", "doing")
        
        try:
            owner, repo = workflow.repository.split("/", 1)
            issue = await provider.get_issue(owner, repo, workflow.issue_number)
            
            # Create PR/MR
            pr_title = f"Fix: {issue.title}"
            pr_body = f"Closes #{workflow.issue_number}\n\n## Summary\n\n{workflow.plan}\n\n## Verification\n\n{workflow.verification_plan}"
            
            # Create PR/MR (idempotently; reuse an existing one for the head branch)
            pr = await self._ensure_pull_request(
                provider=provider,
                owner=owner,
                repo=repo,
                title=pr_title,
                body=pr_body,
                head=workflow.branch,
                base=workflow.base_branch
            )
            
            # Update labels
            await provider.add_label(owner, repo, workflow.issue_number, awaiting_review_label)
            await provider.remove_label(owner, repo, workflow.issue_number, in_progress_label)
            
            # Comment with PR link
            await provider.create_comment(
                owner, repo, workflow.issue_number,
                f"## Agent implementation complete\n\n**PR/MR:** {pr.html_url}\n\n**Branch:** `{workflow.branch}`\n\nAwaiting human review."
            )
            
            workflow.phase = WorkflowPhase.COMPLETED
            self.state.set_workflow(workflow)
            
            return WorkflowResult(success=True, next_phase=WorkflowPhase.COMPLETED)
            
        except Exception as e:
            return WorkflowResult(success=False, error=f"Failed to create PR/MR: {e}")
    
    async def _phase_stuck(self, workflow: WorkflowState) -> WorkflowResult:
        """Stuck phase - wait for human response."""
        # Check if stuck label was removed (handled by discovery.requeue_stuck_workflows)
        # This phase just waits
        return WorkflowResult(success=True, next_phase=WorkflowPhase.STUCK)
    
    def _build_planning_prompt(self, issue, workflow: WorkflowState) -> str:
        """Build planning prompt for OpenCode."""
        return f"""You are an autonomous coding agent. Analyze this issue and create a detailed implementation plan.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}
**Repository:** {workflow.repository}
**Branch:** {workflow.branch}
**Base:** {workflow.base_branch}

## Issue Description
{issue.body}

## Labels
{', '.join(issue.labels)}

## Your Task
Create a detailed implementation plan following this format:

## Agent plan

### Approach
[High-level approach to solving this issue]

### Changes
- [Specific file/component changes]
- [More changes...]

### Verification
- [How to verify the implementation works]
- [Test strategy: automated tests, manual checks, etc.]

### Potential risks
- [Risk 1]
- [Risk 2]

### Branch
`{workflow.branch}`
Base: `{workflow.base_branch}`

Do not implement anything yet. Only create the plan."""
    
    def _build_verification_design_prompt(self, issue, workflow: WorkflowState) -> str:
        """Build verification design prompt."""
        return f"""Based on the issue and your plan, design a verification strategy.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}

## Your Plan
{workflow.plan}

## Your Task
Create a verification strategy following this format:

## Verification plan

### Automated tests
[List specific automated tests to write/run, or state "No automated tests meaningfully cover this change"]

### Manual / visual verification
[If automated tests cannot meaningfully cover the requirement, provide a detailed manual verification plan with specific steps]

### Acceptance criteria
[Specific criteria that must be met for the issue to be considered complete]

Be specific and actionable. Do not create fake tests just to satisfy a test-first rule."""
    
    def _build_write_tests_prompt(self, issue, workflow: WorkflowState) -> str:
        """Build write tests prompt."""
        return f"""Write tests for the issue based on the verification plan.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}

## Verification Plan
{workflow.verification_plan}

## Your Plan
{workflow.plan}

## Your Task
Write tests that:
1. Represent the requirements and acceptance criteria
2. Cover edge cases
3. Will fail before implementation and pass after
4. Are meaningful (not fake tests)

Focus on the automated test portion of the verification plan. Do not implement the feature yet."""
    
    def _build_implementation_prompt(self, issue, workflow: WorkflowState) -> str:
        """Build implementation prompt."""
        return f"""Implement the feature/fix based on your plan and tests.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}

## Your Plan
{workflow.plan}

## Verification Plan
{workflow.verification_plan}

## Your Task
Implement the changes:
1. Follow your plan
2. Make tests pass
3. Commit regularly with conventional commit messages
4. Push to branch `{workflow.branch}`

Do not modify the tests you wrote. Make the implementation satisfy them."""
    
    def _build_verification_prompt(self, issue, workflow: WorkflowState) -> str:
        """Build verification prompt."""
        return f"""Run verification for the implemented changes.

## Issue
**Title:** {issue.title}
**Number:** #{workflow.issue_number}

## Your Plan
{workflow.plan}

## Verification Plan
{workflow.verification_plan}

## Your Task
Run verification:
1. Run all relevant automated tests
2. Run linting/type checks
3. Run build checks
4. Perform manual/visual verification if required
5. Report results

If verification fails, explain what failed and why. The workflow will return to implementing phase."""
    
    def _slugify(self, text: str) -> str:
        """Convert text to slug."""
        import re
        text = text.lower()
        text = re.sub(r'[^a-z0-9]+', '-', text)
        text = text.strip('-')
        return text[:50]
    
    def _has_automated_tests(self, verification_plan: str) -> bool:
        """Check if verification plan includes automated tests."""
        plan_lower = verification_plan.lower()
        return "automated test" in plan_lower and "no automated test" not in plan_lower