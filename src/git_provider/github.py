"""
GitHub Provider Adapter

Implements the GitProvider interface for GitHub API.
"""

import os
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime
from github import Github, GithubException, RateLimitExceededException
from github.Repository import Repository as GHRepository
from github.Issue import Issue as GHIssue
from github.Label import Label as GHLabel
from github.PullRequest import PullRequest as GHPullRequest
from github.Branch import Branch as GHBranch

from . import (
    GitProvider, ProviderError, AuthenticationError, PermissionError,
    NotFoundError, RateLimitError, NetworkError, ValidationError,
    ConflictError, TemporaryProviderError,
    Repository, Issue, Comment, Label, PullRequest, Branch, CIStatus
)

logger = logging.getLogger(__name__)


class GitHubProvider(GitProvider):
    """GitHub API provider implementation."""
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.token = config.get("token") or os.environ.get("GITHUB_TOKEN")
        self.base_url = config.get("base_url", "https://api.github.com")
        self._client: Optional[Github] = None
        self._rate_limit_remaining = 5000
        self._rate_limit_reset = 0
    
    async def authenticate(self) -> bool:
        """Authenticate with GitHub."""
        if not self.token:
            raise AuthenticationError("GitHub token not configured")
        
        try:
            self._client = Github(
                self.token,
                base_url=self.base_url,
                timeout=30,
                retry=3
            )
            # Test authentication
            user = self._client.get_user()
            logger.info(f"Authenticated as GitHub user: {user.login}")
            return True
        except GithubException as e:
            if e.status == 401:
                raise AuthenticationError(f"GitHub authentication failed: {e.data.get('message', str(e))}", e)
            elif e.status == 403:
                raise PermissionError(f"GitHub permission denied: {e.data.get('message', str(e))}", e)
            else:
                raise ProviderError(f"GitHub authentication error: {e}", "authentication_failure", e)
        except Exception as e:
            raise NetworkError(f"GitHub connection failed: {e}", e)
    
    def _check_rate_limit(self):
        """Check and update rate limit info."""
        if self._client:
            try:
                rate_limit = self._client.get_rate_limit()
                self._rate_limit_remaining = rate_limit.core.remaining
                self._rate_limit_reset = int(rate_limit.core.reset.timestamp())
            except Exception:
                pass
    
    async def check_rate_limit(self) -> Dict[str, Any]:
        """Check current rate limit status."""
        self._check_rate_limit()
        return {
            "remaining": self._rate_limit_remaining,
            "reset_at": self._rate_limit_reset,
            "limit": 5000
        }
    
    def _handle_github_exception(self, e: GithubException, operation: str):
        """Convert GitHub exceptions to provider errors."""
        if isinstance(e, RateLimitExceededException):
            reset_time = e.rate_limit.reset.timestamp() if e.rate_limit else 0
            raise RateLimitError(
                f"GitHub rate limit exceeded for {operation}",
                e,
                retry_after=int(reset_time - datetime.now().timestamp()) if reset_time else None
            )
        elif e.status == 401:
            raise AuthenticationError(f"GitHub authentication failed during {operation}: {e.data.get('message', str(e))}", e)
        elif e.status == 403:
            raise PermissionError(f"GitHub permission denied during {operation}: {e.data.get('message', str(e))}", e)
        elif e.status == 404:
            raise NotFoundError(f"GitHub resource not found during {operation}: {e.data.get('message', str(e))}", e)
        elif e.status == 422:
            raise ValidationError(f"GitHub validation failed during {operation}: {e.data.get('message', str(e))}", e)
        elif e.status == 409:
            raise ConflictError(f"GitHub conflict during {operation}: {e.data.get('message', str(e))}", e)
        elif 500 <= e.status < 600:
            raise TemporaryProviderError(f"GitHub server error during {operation}: {e.data.get('message', str(e))}", e)
        else:
            raise ProviderError(f"GitHub error during {operation}: {e.data.get('message', str(e))}", "unknown", e)
    
    def _repo_to_model(self, repo: GHRepository) -> Repository:
        """Convert GitHub repository to model."""
        return Repository(
            id=str(repo.id),
            name=repo.name,
            full_name=repo.full_name,
            owner=repo.owner.login,
            private=repo.private,
            default_branch=repo.default_branch,
            clone_url=repo.clone_url,
            ssh_url=repo.ssh_url,
            html_url=repo.html_url,
            description=repo.description
        )
    
    def _issue_to_model(self, issue: GHIssue, repo_full_name: str) -> Issue:
        """Convert GitHub issue to model."""
        return Issue(
            number=issue.number,
            title=issue.title,
            body=issue.body or "",
            state=issue.state,
            labels=[label.name for label in issue.labels],
            assignees=[assignee.login for assignee in issue.assignees] if issue.assignees else [],
            author=issue.user.login if issue.user else "unknown",
            created_at=issue.created_at.isoformat() if issue.created_at else "",
            updated_at=issue.updated_at.isoformat() if issue.updated_at else "",
            html_url=issue.html_url,
            comments_count=issue.comments,
            repository=repo_full_name
        )
    
    def _comment_to_model(self, comment) -> Comment:
        """Convert GitHub comment to model."""
        return Comment(
            id=str(comment.id),
            body=comment.body or "",
            author=comment.user.login if comment.user else "unknown",
            created_at=comment.created_at.isoformat() if comment.created_at else "",
            updated_at=comment.updated_at.isoformat() if comment.updated_at else "",
            html_url=comment.html_url
        )
    
    def _label_to_model(self, label: GHLabel) -> Label:
        """Convert GitHub label to model."""
        return Label(
            name=label.name,
            color=label.color,
            description=label.description
        )
    
    def _pr_to_model(self, pr: GHPullRequest, repo_full_name: str) -> PullRequest:
        """Convert GitHub PR to model."""
        return PullRequest(
            number=pr.number,
            title=pr.title,
            body=pr.body or "",
            state=pr.state,
            head_branch=pr.head.ref,
            base_branch=pr.base.ref,
            author=pr.user.login if pr.user else "unknown",
            created_at=pr.created_at.isoformat() if pr.created_at else "",
            updated_at=pr.updated_at.isoformat() if pr.updated_at else "",
            html_url=pr.html_url,
            draft=pr.draft,
            mergeable=pr.mergeable,
            merged=pr.merged,
            merged_at=pr.merged_at.isoformat() if pr.merged_at else None,
            repository=repo_full_name
        )
    
    def _branch_to_model(self, branch: GHBranch) -> Branch:
        """Convert GitHub branch to model."""
        return Branch(
            name=branch.name,
            commit_sha=branch.commit.sha if branch.commit else "",
            protected=branch.protected
        )
    
    async def get_repository(self, owner: str, repo: str) -> Repository:
        """Get repository information."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            return self._repo_to_model(gh_repo)
        except GithubException as e:
            self._handle_github_exception(e, f"get_repository({owner}/{repo})")
    
    async def list_repositories(self, owner: str = None) -> List[Repository]:
        """List accessible repositories."""
        try:
            if owner:
                user = self._client.get_user(owner)
                repos = user.get_repos(type="all", sort="updated")
            else:
                repos = self._client.get_user().get_repos(type="all", sort="updated")
            return [self._repo_to_model(r) for r in repos]
        except GithubException as e:
            self._handle_github_exception(e, f"list_repositories({owner})")
    
    async def get_issue(self, owner: str, repo: str, number: int) -> Issue:
        """Get a single issue by number."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            gh_issue = gh_repo.get_issue(number)
            return self._issue_to_model(gh_issue, f"{owner}/{repo}")
        except GithubException as e:
            self._handle_github_exception(e, f"get_issue({owner}/{repo}#{number})")
    
    async def list_issues(self, owner: str, repo: str, state: str = "open", labels: List[str] = None, limit: int = 100) -> List[Issue]:
        """List issues with optional filtering."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            kwargs = {"state": state}
            if labels:
                kwargs["labels"] = [self._client.get_repo(f"{owner}/{repo}").get_label(l) for l in labels]
            
            issues = gh_repo.get_issues(**kwargs)
            result = []
            for i, issue in enumerate(issues):
                if i >= limit:
                    break
                result.append(self._issue_to_model(issue, f"{owner}/{repo}"))
            return result
        except GithubException as e:
            self._handle_github_exception(e, f"list_issues({owner}/{repo})")
    
    async def list_eligible_issues(self, owner: str, repo: str, label: str = "agent", limit: int = 100) -> List[Issue]:
        """List issues eligible for autonomous work."""
        return await self.list_issues(owner, repo, state="open", labels=[label], limit=limit)
    
    async def get_issue_comments(self, owner: str, repo: str, number: int) -> List[Comment]:
        """Get all comments on an issue."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            gh_issue = gh_repo.get_issue(number)
            comments = gh_issue.get_comments()
            return [self._comment_to_model(c) for c in comments]
        except GithubException as e:
            self._handle_github_exception(e, f"get_issue_comments({owner}/{repo}#{number})")
    
    async def add_label(self, owner: str, repo: str, number: int, label: str) -> bool:
        """Add a label to an issue."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            gh_issue = gh_repo.get_issue(number)
            gh_issue.add_to_labels(label)
            return True
        except GithubException as e:
            self._handle_github_exception(e, f"add_label({owner}/{repo}#{number}, {label})")
    
    async def remove_label(self, owner: str, repo: str, number: int, label: str) -> bool:
        """Remove a label from an issue."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            gh_issue = gh_repo.get_issue(number)
            gh_issue.remove_from_labels(label)
            return True
        except GithubException as e:
            self._handle_github_exception(e, f"remove_label({owner}/{repo}#{number}, {label})")
    
    async def create_comment(self, owner: str, repo: str, number: int, body: str) -> Comment:
        """Create a comment on an issue."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            gh_issue = gh_repo.get_issue(number)
            comment = gh_issue.create_comment(body)
            return self._comment_to_model(comment)
        except GithubException as e:
            self._handle_github_exception(e, f"create_comment({owner}/{repo}#{number})")
    
    async def create_pull_request(self, owner: str, repo: str, title: str, body: str, head: str, base: str, draft: bool = False) -> PullRequest:
        """Create a pull request."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            pr = gh_repo.create_pull(
                title=title,
                body=body,
                head=head,
                base=base,
                draft=draft
            )
            return self._pr_to_model(pr, f"{owner}/{repo}")
        except GithubException as e:
            self._handle_github_exception(e, f"create_pull_request({owner}/{repo})")
    
    async def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        """Get a pull request by number."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            pr = gh_repo.get_pull(number)
            return self._pr_to_model(pr, f"{owner}/{repo}")
        except GithubException as e:
            self._handle_github_exception(e, f"get_pull_request({owner}/{repo}#{number})")
    
    async def list_pull_requests(self, owner: str, repo: str, state: str = "open", head: str = None, base: str = None) -> List[PullRequest]:
        """List pull requests."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            kwargs = {"state": state}
            if head:
                kwargs["head"] = head
            if base:
                kwargs["base"] = base
            
            prs = gh_repo.get_pulls(**kwargs)
            return [self._pr_to_model(pr, f"{owner}/{repo}") for pr in prs]
        except GithubException as e:
            self._handle_github_exception(e, f"list_pull_requests({owner}/{repo})")
    
    async def update_pull_request(self, owner: str, repo: str, number: int, title: str = None, body: str = None, state: str = None) -> PullRequest:
        """Update a pull request."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            pr = gh_repo.get_pull(number)
            
            if title is not None:
                pr.edit(title=title)
            if body is not None:
                pr.edit(body=body)
            if state == "closed":
                pr.edit(state="closed")
            
            # Refresh
            pr = gh_repo.get_pull(number)
            return self._pr_to_model(pr, f"{owner}/{repo}")
        except GithubException as e:
            self._handle_github_exception(e, f"update_pull_request({owner}/{repo}#{number})")
    
    async def merge_pull_request(self, owner: str, repo: str, number: int, method: str = "merge") -> bool:
        """Merge a pull request."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            pr = gh_repo.get_pull(number)
            pr.merge(merge_method=method)
            return True
        except GithubException as e:
            self._handle_github_exception(e, f"merge_pull_request({owner}/{repo}#{number})")
    
    async def get_branch(self, owner: str, repo: str, branch: str) -> Branch:
        """Get branch information."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            gh_branch = gh_repo.get_branch(branch)
            return self._branch_to_model(gh_branch)
        except GithubException as e:
            self._handle_github_exception(e, f"get_branch({owner}/{repo}/{branch})")
    
    async def list_branches(self, owner: str, repo: str, limit: int = 100) -> List[Branch]:
        """List branches."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            branches = gh_repo.get_branches()
            result = []
            for i, branch in enumerate(branches):
                if i >= limit:
                    break
                result.append(self._branch_to_model(branch))
            return result
        except GithubException as e:
            self._handle_github_exception(e, f"list_branches({owner}/{repo})")
    
    async def create_branch(self, owner: str, repo: str, branch: str, from_branch: str = None) -> Branch:
        """Create a new branch."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            if from_branch is None:
                from_branch = gh_repo.default_branch
            
            source_branch = gh_repo.get_branch(from_branch)
            gh_repo.create_git_ref(ref=f"refs/heads/{branch}", sha=source_branch.commit.sha)
            
            new_branch = gh_repo.get_branch(branch)
            return self._branch_to_model(new_branch)
        except GithubException as e:
            self._handle_github_exception(e, f"create_branch({owner}/{repo}/{branch})")
    
    async def get_ci_status(self, owner: str, repo: str, ref: str) -> List[CIStatus]:
        """Get CI/CD status for a commit/ref."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            commit = gh_repo.get_commit(ref)
            statuses = commit.get_statuses()
            
            result = []
            for status in statuses:
                result.append(CIStatus(
                    state=status.state,
                    description=status.description,
                    target_url=status.target_url,
                    context=status.context,
                    created_at=status.created_at.isoformat() if status.created_at else None,
                    updated_at=status.updated_at.isoformat() if status.updated_at else None
                ))
            return result
        except GithubException as e:
            self._handle_github_exception(e, f"get_ci_status({owner}/{repo}/{ref})")
    
    async def get_labels(self, owner: str, repo: str) -> List[Label]:
        """Get all labels for a repository."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            labels = gh_repo.get_labels()
            return [self._label_to_model(l) for l in labels]
        except GithubException as e:
            self._handle_github_exception(e, f"get_labels({owner}/{repo})")
    
    async def create_label(self, owner: str, repo: str, name: str, color: str, description: str = None) -> Label:
        """Create a new label."""
        try:
            gh_repo = self._client.get_repo(f"{owner}/{repo}")
            label = gh_repo.create_label(name=name, color=color, description=description or "")
            return self._label_to_model(label)
        except GithubException as e:
            self._handle_github_exception(e, f"create_label({owner}/{repo}, {name})")