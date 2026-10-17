"""
Issue Discovery

Discovers eligible issues from Git provider and queues them for processing.
"""

import logging

from git_provider import GitProvider, ProviderError
from orchestrator.config import OrchestratorConfig
from orchestrator.providers import ProviderManager
from orchestrator.state import OrchestratorState, WorkflowPhase, WorkflowState

logger = logging.getLogger(__name__)


class IssueDiscovery:
    """Discovers and queues eligible issues."""

    def __init__(self, config: OrchestratorConfig, state: OrchestratorState, providers: ProviderManager | None = None):
        """Create a discovery that queues eligible issues into the shared state."""
        self.config = config
        self.state = state
        self.providers = providers or ProviderManager(config)

    async def _get_provider(self, repository: str) -> GitProvider:
        """Get the provider that owns a repository."""
        return await self.providers.provider_for(repository)

    async def discover_and_queue(self) -> int:
        """Discover eligible issues and queue them for processing."""
        if not self.config.git_provider.repositories:
            logger.debug("No repositories configured for discovery")
            return 0

        eligible_label = self.config.git_provider.labels.get("eligible", "agent")
        in_progress_label = self.config.git_provider.labels.get("in_progress", "doing")
        stuck_label = self.config.git_provider.labels.get("stuck", "stuck")
        awaiting_review_label = self.config.git_provider.labels.get("awaiting_review", "awaiting review")

        queued_count = 0

        for repo_spec in self.config.git_provider.repositories:
            try:
                owner, repo = repo_spec.split("/", 1)
                provider = await self._get_provider(repo_spec)

                # Discover eligible issues
                issues = await provider.list_eligible_issues(
                    owner, repo, label=eligible_label, limit=self.config.git_provider.max_issues_per_poll
                )

                for issue in issues:
                    # Check if already in progress or stuck
                    existing = self.state.get_workflow(repo_spec, issue.number)

                    if existing:
                        logger.info(
                            f"Issue {repo_spec}#{issue.number} already in phase {existing.phase.value}, skipping"
                        )
                        continue

                    # Check if issue has in_progress or stuck label (another instance might be working on it)
                    if (
                        in_progress_label in issue.labels
                        or stuck_label in issue.labels
                        or awaiting_review_label in issue.labels
                    ):
                        logger.info(f"Issue {repo_spec}#{issue.number} has active label, skipping")
                        continue

                    # Create new workflow
                    workflow = WorkflowState(
                        issue_number=issue.number,
                        repository=repo_spec,
                        project=self._get_project_for_repo(repo_spec),
                        phase=WorkflowPhase.NEW,
                    )
                    self.state.set_workflow(workflow)
                    queued_count += 1
                    logger.info(f"Queued issue {repo_spec}#{issue.number}: {issue.title}")

            except ProviderError as e:
                logger.error(f"Provider error discovering issues in {repo_spec}: {e}")
            except Exception as e:
                logger.error(f"Error discovering issues in {repo_spec}: {e}")

        if queued_count > 0:
            await self.state.save()

        return queued_count

    def _get_project_for_repo(self, repo_spec: str) -> str:
        """Get project name for repository."""
        # For now, use the repo name as project
        # This could be made configurable
        return repo_spec.split("/")[-1]

    async def requeue_stuck_workflows(self) -> int:
        """Requeue workflows that were stuck but now have human response."""
        stuck_workflows = self.state.list_stuck_workflows()
        requeued = 0

        for workflow in stuck_workflows:
            # Check if issue still has stuck label
            try:
                owner, repo = workflow.repository.split("/", 1)
                provider = await self._get_provider(workflow.repository)
                issue = await provider.get_issue(owner, repo, workflow.issue_number)

                stuck_label = self.config.git_provider.labels.get("stuck", "stuck")
                if stuck_label not in issue.labels:
                    # Human removed stuck label, resume workflow
                    workflow.phase = WorkflowPhase.PLANNING  # Resume from planning
                    workflow.stuck_reason = ""
                    self.state.set_workflow(workflow)
                    requeued += 1
                    logger.info(f"Resumed stuck workflow {workflow.repository}#{workflow.issue_number}")
            except Exception as e:
                logger.error(f"Error checking stuck workflow {workflow.repository}#{workflow.issue_number}: {e}")

        if requeued > 0:
            await self.state.save()

        return requeued
