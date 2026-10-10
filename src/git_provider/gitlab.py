"""
GitLab Provider Adapter

Implements the GitProvider interface for GitLab API.
"""

import logging
import os
from typing import Any

import gitlab
from gitlab.exceptions import (
    GitlabAuthenticationError,
    GitlabCreateError,
    GitlabDeleteError,
    GitlabGetError,
    GitlabRateLimitError,
    GitlabUpdateError,
)

from . import (
    AuthenticationError,
    Branch,
    CIStatus,
    Comment,
    ConflictError,
    GitProvider,
    Issue,
    Label,
    NetworkError,
    NotFoundError,
    PermissionError,
    ProviderError,
    PullRequest,
    RateLimitError,
    Repository,
    TemporaryProviderError,
    ValidationError,
)

logger = logging.getLogger(__name__)


class GitLabProvider(GitProvider):
    """GitLab API provider implementation."""

    def __init__(self, config: dict[str, Any]):
        super().__init__(config)
        self.token = config.get("token") or os.environ.get("GITLAB_TOKEN")
        self.url = config.get("url", "https://gitlab.com")
        self._client: gitlab.Gitlab | None = None

    async def authenticate(self) -> bool:
        """Authenticate with GitLab."""
        if not self.token:
            raise AuthenticationError("GitLab token not configured")

        try:
            self._client = gitlab.Gitlab(url=self.url, private_token=self.token, timeout=30)
            self._client.auth()
            user = self._client.user
            logger.info(f"Authenticated as GitLab user: {user.username}")
            return True
        except GitlabAuthenticationError as e:
            raise AuthenticationError(f"GitLab authentication failed: {e}", e) from e
        except Exception as e:
            raise NetworkError(f"GitLab connection failed: {e}", e) from e

    def _handle_gitlab_exception(self, e: Exception, operation: str):
        """Convert GitLab exceptions to provider errors."""
        if isinstance(e, GitlabAuthenticationError):
            raise AuthenticationError(f"GitLab authentication failed during {operation}: {e}", e)
        elif isinstance(e, GitlabRateLimitError):
            raise RateLimitError(f"GitLab rate limit exceeded for {operation}: {e}", e)
        elif isinstance(e, GitlabGetError):
            if e.response_code == 403:
                raise PermissionError(f"GitLab permission denied during {operation}: {e}", e)
            elif e.response_code == 404:
                raise NotFoundError(f"GitLab resource not found during {operation}: {e}", e)
            else:
                raise ProviderError(f"GitLab error during {operation}: {e}", "unknown", e)
        elif isinstance(e, (GitlabCreateError, GitlabUpdateError, GitlabDeleteError)):
            if e.response_code == 403:
                raise PermissionError(f"GitLab permission denied during {operation}: {e}", e)
            elif e.response_code == 404:
                raise NotFoundError(f"GitLab resource not found during {operation}: {e}", e)
            elif e.response_code == 409:
                raise ConflictError(f"GitLab conflict during {operation}: {e}", e)
            elif e.response_code == 422:
                raise ValidationError(f"GitLab validation failed during {operation}: {e}", e)
            elif 500 <= e.response_code < 600:
                raise TemporaryProviderError(f"GitLab server error during {operation}: {e}", e)
            else:
                raise ProviderError(f"GitLab error during {operation}: {e}", "unknown", e)
        else:
            raise ProviderError(f"GitLab error during {operation}: {e}", "unknown", e)

    def _project_to_model(self, project) -> Repository:
        """Convert GitLab project to model."""
        return Repository(
            id=str(project.id),
            name=project.name,
            full_name=project.path_with_namespace,
            owner=project.namespace["path"] if isinstance(project.namespace, dict) else project.namespace.path,
            private=project.visibility == "private",
            default_branch=project.default_branch,
            clone_url=project.http_url_to_repo,
            ssh_url=project.ssh_url_to_repo,
            html_url=project.web_url,
            description=project.description,
        )

    def _issue_to_model(self, issue, project_path: str) -> Issue:
        """Convert GitLab issue to model."""
        labels = (
            [label["name"] if isinstance(label, dict) else label.name for label in issue.labels] if issue.labels else []
        )
        assignees = (
            [assignee["username"] if isinstance(assignee, dict) else assignee.username for assignee in issue.assignees]
            if issue.assignees
            else []
        )

        return Issue(
            number=issue.iid,
            title=issue.title,
            body=issue.description or "",
            state=issue.state,
            labels=labels,
            assignees=assignees,
            author=issue.author["username"] if isinstance(issue.author, dict) else issue.author.username,
            created_at=issue.created_at,
            updated_at=issue.updated_at,
            html_url=issue.web_url,
            comments_count=issue.user_notes_count,
            repository=project_path,
        )

    def _comment_to_model(self, note, project_path: str) -> Comment:
        """Convert GitLab note to model."""
        return Comment(
            id=str(note.id),
            body=note.body or "",
            author=note.author["username"] if isinstance(note.author, dict) else note.author.username,
            created_at=note.created_at,
            updated_at=note.updated_at,
            html_url=f"{self.url}/{project_path}/-/issues/{note.noteable_iid}#note_{note.id}",
        )

    def _label_to_model(self, label) -> Label:
        """Convert GitLab label to model."""
        return Label(name=label.name, color=label.color, description=label.description)

    def _mr_to_model(self, mr, project_path: str) -> PullRequest:
        """Convert GitLab MR to model."""
        return PullRequest(
            number=mr.iid,
            title=mr.title,
            body=mr.description or "",
            state=mr.state,
            head_branch=mr.source_branch,
            base_branch=mr.target_branch,
            author=mr.author["username"] if isinstance(mr.author, dict) else mr.author.username,
            created_at=mr.created_at,
            updated_at=mr.updated_at,
            html_url=mr.web_url,
            draft=mr.draft,
            mergeable=mr.merge_status == "can_be_merged",
            merged=mr.state == "merged",
            merged_at=mr.merged_at,
            repository=project_path,
        )

    def _branch_to_model(self, branch) -> Branch:
        """Convert GitLab branch to model."""
        return Branch(
            name=branch.name,
            commit_sha=branch.commit["id"] if isinstance(branch.commit, dict) else branch.commit.id,
            protected=branch.protected,
        )

    async def get_repository(self, owner: str, repo: str) -> Repository:
        """Get repository information."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            return self._project_to_model(project)
        except Exception as e:
            self._handle_gitlab_exception(e, f"get_repository({owner}/{repo})")

    async def list_repositories(self, owner: str | None = None) -> list[Repository]:
        """List accessible repositories."""
        try:
            if owner:
                projects = self._client.projects.list(owned=True, membership=True, search=owner, get_all=True)
            else:
                projects = self._client.projects.list(membership=True, order_by="last_activity_at", get_all=True)
            return [self._project_to_model(p) for p in projects]
        except Exception as e:
            self._handle_gitlab_exception(e, f"list_repositories({owner})")

    async def get_issue(self, owner: str, repo: str, number: int) -> Issue:
        """Get a single issue by number."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            issue = project.issues.get(number)
            return self._issue_to_model(issue, f"{owner}/{repo}")
        except Exception as e:
            self._handle_gitlab_exception(e, f"get_issue({owner}/{repo}#{number})")

    async def list_issues(
        self, owner: str, repo: str, state: str = "open", labels: list[str] | None = None, limit: int = 100
    ) -> list[Issue]:
        """List issues with optional filtering."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            kwargs = {"state": state}
            if labels:
                kwargs["labels"] = labels

            issues = project.issues.list(**kwargs, get_all=False, per_page=limit)
            return [self._issue_to_model(issue, f"{owner}/{repo}") for issue in issues[:limit]]
        except Exception as e:
            self._handle_gitlab_exception(e, f"list_issues({owner}/{repo})")

    async def list_eligible_issues(self, owner: str, repo: str, label: str = "agent", limit: int = 100) -> list[Issue]:
        """List issues eligible for autonomous work."""
        return await self.list_issues(owner, repo, state="open", labels=[label], limit=limit)

    async def get_issue_comments(self, owner: str, repo: str, number: int) -> list[Comment]:
        """Get all comments on an issue."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            issue = project.issues.get(number)
            notes = issue.notes.list(get_all=True)
            return [self._comment_to_model(note, f"{owner}/{repo}") for note in notes]
        except Exception as e:
            self._handle_gitlab_exception(e, f"get_issue_comments({owner}/{repo}#{number})")

    async def add_label(self, owner: str, repo: str, number: int, label: str) -> bool:
        """Add a label to an issue."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            issue = project.issues.get(number)
            current_labels = (
                [item["name"] if isinstance(item, dict) else item.name for item in issue.labels] if issue.labels else []
            )
            if label not in current_labels:
                current_labels.append(label)
                issue.labels = current_labels
                issue.save()
            return True
        except Exception as e:
            self._handle_gitlab_exception(e, f"add_label({owner}/{repo}#{number}, {label})")

    async def remove_label(self, owner: str, repo: str, number: int, label: str) -> bool:
        """Remove a label from an issue."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            issue = project.issues.get(number)
            current_labels = (
                [item["name"] if isinstance(item, dict) else item.name for item in issue.labels] if issue.labels else []
            )
            if label in current_labels:
                current_labels.remove(label)
                issue.labels = current_labels
                issue.save()
            return True
        except Exception as e:
            self._handle_gitlab_exception(e, f"remove_label({owner}/{repo}#{number}, {label})")

    async def create_comment(self, owner: str, repo: str, number: int, body: str) -> Comment:
        """Create a comment on an issue."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            issue = project.issues.get(number)
            note = issue.notes.create({"body": body})
            return self._comment_to_model(note, f"{owner}/{repo}")
        except Exception as e:
            self._handle_gitlab_exception(e, f"create_comment({owner}/{repo}#{number})")

    async def create_pull_request(
        self, owner: str, repo: str, title: str, body: str, head: str, base: str, draft: bool = False
    ) -> PullRequest:
        """Create a merge request."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            mr = project.mergerequests.create(
                {"source_branch": head, "target_branch": base, "title": title, "description": body, "draft": draft}
            )
            return self._mr_to_model(mr, f"{owner}/{repo}")
        except Exception as e:
            self._handle_gitlab_exception(e, f"create_pull_request({owner}/{repo})")

    async def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        """Get a merge request by number."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            mr = project.mergerequests.get(number)
            return self._mr_to_model(mr, f"{owner}/{repo}")
        except Exception as e:
            self._handle_gitlab_exception(e, f"get_pull_request({owner}/{repo}#{number})")

    async def list_pull_requests(
        self, owner: str, repo: str, state: str = "open", head: str | None = None, base: str | None = None
    ) -> list[PullRequest]:
        """List merge requests."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            kwargs = {"state": state}
            if head:
                kwargs["source_branch"] = head
            if base:
                kwargs["target_branch"] = base

            mrs = project.mergerequests.list(**kwargs, get_all=True)
            return [self._mr_to_model(mr, f"{owner}/{repo}") for mr in mrs]
        except Exception as e:
            self._handle_gitlab_exception(e, f"list_pull_requests({owner}/{repo})")

    async def update_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
    ) -> PullRequest:
        """Update a merge request."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            mr = project.mergerequests.get(number)

            if title is not None:
                mr.title = title
            if body is not None:
                mr.description = body
            if state == "closed":
                mr.state_event = "close"

            mr.save()
            mr = project.mergerequests.get(number)
            return self._mr_to_model(mr, f"{owner}/{repo}")
        except Exception as e:
            self._handle_gitlab_exception(e, f"update_pull_request({owner}/{repo}#{number})")

    async def merge_pull_request(self, owner: str, repo: str, number: int, method: str = "merge") -> bool:
        """Merge a merge request."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            mr = project.mergerequests.get(number)
            mr.merge(merge_method=method)
            return True
        except Exception as e:
            self._handle_gitlab_exception(e, f"merge_pull_request({owner}/{repo}#{number})")

    async def get_branch(self, owner: str, repo: str, branch: str) -> Branch:
        """Get branch information."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            gl_branch = project.branches.get(branch)
            return self._branch_to_model(gl_branch)
        except Exception as e:
            self._handle_gitlab_exception(e, f"get_branch({owner}/{repo}/{branch})")

    async def list_branches(self, owner: str, repo: str, limit: int = 100) -> list[Branch]:
        """List branches."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            branches = project.branches.list(get_all=True, per_page=limit)
            return [self._branch_to_model(b) for b in branches[:limit]]
        except Exception as e:
            self._handle_gitlab_exception(e, f"list_branches({owner}/{repo})")

    async def create_branch(self, owner: str, repo: str, branch: str, from_branch: str | None = None) -> Branch:
        """Create a new branch."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            if from_branch is None:
                from_branch = project.default_branch

            project.branches.create({"branch": branch, "ref": from_branch})
            gl_branch = project.branches.get(branch)
            return self._branch_to_model(gl_branch)
        except Exception as e:
            self._handle_gitlab_exception(e, f"create_branch({owner}/{repo}/{branch})")

    async def get_ci_status(self, owner: str, repo: str, ref: str) -> list[CIStatus]:
        """Get CI/CD status for a commit/ref."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            commit = project.commits.get(ref)
            statuses = commit.statuses()

            result = []
            for status in statuses:
                result.append(
                    CIStatus(
                        state=status["status"],
                        description=status.get("description"),
                        target_url=status.get("target_url"),
                        context=status.get("name"),
                        created_at=status.get("created_at"),
                        updated_at=status.get("updated_at"),
                    )
                )
            return result
        except Exception as e:
            self._handle_gitlab_exception(e, f"get_ci_status({owner}/{repo}/{ref})")

    async def get_labels(self, owner: str, repo: str) -> list[Label]:
        """Get all labels for a repository."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            labels = project.labels.list(get_all=True)
            return [self._label_to_model(label) for label in labels]
        except Exception as e:
            self._handle_gitlab_exception(e, f"get_labels({owner}/{repo})")

    async def create_label(self, owner: str, repo: str, name: str, color: str, description: str | None = None) -> Label:
        """Create a new label."""
        try:
            project = self._client.projects.get(f"{owner}/{repo}")
            label = project.labels.create({"name": name, "color": color, "description": description or ""})
            return self._label_to_model(label)
        except Exception as e:
            self._handle_gitlab_exception(e, f"create_label({owner}/{repo}, {name})")

    async def check_rate_limit(self) -> dict[str, Any]:
        """Check current rate limit status."""
        # GitLab doesn't expose rate limit in the same way
        return {"remaining": "unknown", "reset_at": 0, "limit": "unknown"}
