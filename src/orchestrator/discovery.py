"""
Issue Discovery

Discovers eligible issues from Git provider and queues them for processing.
"""

import logging
from typing import List, Optional
from dataclasses import dataclass

from orchestrator.config import OrchestratorConfig
from orchestrator.state import OrchestratorState, WorkflowState, WorkflowPhase
from git_provider import create_provider, ProviderType, GitProvider, ProviderError

logger = logging.getLogger(__name__)


@dataclass
class DiscoveredIssue:
    """An issue discovered as eligible for processing."""
    repository: str
    issue_number: int
    title: str
    labels: List[str]


class IssueDiscovery:
    """Discovers and queues eligible issues."""
    
    def __init__(self, config: OrchestratorConfig, state: OrchestratorState):
        self.config = config
        self.state = state
        self._provider: Optional[GitProvider] = None
    
    async def _get_provider(self) -> GitProvider:
        """Get or create Git provider."""
        if self._provider is None:
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
    
    async def discover_and_queue(self) -> int:
        """Discover eligible issues and queue them for processing."""
        if not self.config.git_provider.repositories:
            logger.debug("No repositories configured for discovery")
            return 0
        
        provider = await self._get_provider()
        eligible_label = self.config.git_provider.labels.get("eligible", "agent")
        in_progress_label = self.config.git_provider.labels.get("in_progress", "doing")
        stuck_label = self.config.git_provider.labels.get("stuck", "stuck")
        awaiting_review_label = self.config.git_provider.labels.get("awaiting_review", "awaiting review")
        
        queued_count = 0
        
        for repo_spec in self.config.git_provider.repositories:
            try:
                owner, repo = repo_spec.split("/", 1)
                
                # Check if we already have an active workflow for this repo
                active_workflows = self.state.list_active_workflows()
                repo_active = any(wf.repository == repo_spec for wf in active_workflows)
                
                # Discover eligible issues
                issues = await provider.list_eligible_issues(
                    owner, repo, 
                    label=eligible_label,
                    limit=self.config.git_provider.max_issues_per_poll
                )
                
                for issue in issues:
                    # Check if already in progress or stuck
                    existing = self.state.get_workflow(repo_spec, issue.number)
                    
                    if existing:
                        logger.info(f"Issue {repo_spec}#{issue.number} already in phase {existing.phase.value}, skipping")
                        continue
                    
                    # Check if issue has in_progress or stuck label (another instance might be working on it)
                    if in_progress_label in issue.labels or stuck_label in issue.labels or awaiting_review_label in issue.labels:
                        logger.info(f"Issue {repo_spec}#{issue.number} has active label, skipping")
                        continue
                    
                    # Create new workflow
                    workflow = WorkflowState(
                        issue_number=issue.number,
                        repository=repo_spec,
                        project=self._get_project_for_repo(repo_spec),
                        phase=WorkflowPhase.NEW
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
            provider = await self._get_provider()
            try:
                owner, repo = workflow.repository.split("/", 1)
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