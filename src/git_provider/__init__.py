"""
Git Provider Abstraction Layer

This module provides a unified interface for GitHub and GitLab operations,
hiding provider-specific API details from the orchestrator.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ProviderType(Enum):
    GITHUB = "github"
    GITLAB = "gitlab"


class ProviderError(Exception):
    """Base exception for provider errors."""

    def __init__(self, message: str, category: str = "unknown", original: Exception | None = None):
        super().__init__(message)
        self.category = category
        self.original = original


class AuthenticationError(ProviderError):
    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message, "authentication_failure", original)


class PermissionError(ProviderError):
    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message, "permission_denied", original)


class NotFoundError(ProviderError):
    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message, "not_found", original)


class RateLimitError(ProviderError):
    def __init__(self, message: str, original: Exception | None = None, retry_after: int | None = None):
        super().__init__(message, "rate_limited", original)
        self.retry_after = retry_after


class NetworkError(ProviderError):
    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message, "network_failure", original)


class ValidationError(ProviderError):
    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message, "validation_failure", original)


class ConflictError(ProviderError):
    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message, "conflict", original)


class TemporaryProviderError(ProviderError):
    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message, "temporary_provider_failure", original)


class UnsupportedOperationError(ProviderError):
    def __init__(self, message: str, original: Exception | None = None):
        super().__init__(message, "unsupported_operation", original)


@dataclass
class Repository:
    """Repository information."""

    id: str
    name: str
    full_name: str
    owner: str
    private: bool
    default_branch: str
    clone_url: str
    ssh_url: str
    html_url: str
    description: str | None = None


@dataclass
class Issue:
    """Issue information."""

    number: int
    title: str
    body: str
    state: str  # open, closed
    labels: list[str]
    assignees: list[str]
    author: str
    created_at: str
    updated_at: str
    html_url: str
    comments_count: int
    repository: str


@dataclass
class Comment:
    """Comment information."""

    id: str
    body: str
    author: str
    created_at: str
    updated_at: str
    html_url: str


@dataclass
class Label:
    """Label information."""

    name: str
    color: str
    description: str | None = None


@dataclass
class PullRequest:
    """Pull/Merge Request information."""

    number: int
    title: str
    body: str
    state: str  # open, closed, merged
    head_branch: str
    base_branch: str
    author: str
    created_at: str
    updated_at: str
    html_url: str
    draft: bool
    mergeable: bool | None = None
    merged: bool = False
    merged_at: str | None = None
    repository: str = ""


@dataclass
class Branch:
    """Branch information."""

    name: str
    commit_sha: str
    protected: bool = False


@dataclass
class CIStatus:
    """CI/CD status information."""

    state: str  # pending, success, failure, error
    description: str | None = None
    target_url: str | None = None
    context: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class GitProvider(ABC):
    """Abstract base class for Git providers."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self._client = None

    @abstractmethod
    async def authenticate(self) -> bool:
        """Authenticate with the provider."""

    @abstractmethod
    async def get_repository(self, owner: str, repo: str) -> Repository:
        """Get repository information."""

    @abstractmethod
    async def list_repositories(self, owner: str | None = None) -> list[Repository]:
        """List accessible repositories."""

    # Issue operations
    @abstractmethod
    async def get_issue(self, owner: str, repo: str, number: int) -> Issue:
        """Get a single issue by number."""

    @abstractmethod
    async def list_issues(
        self, owner: str, repo: str, state: str = "open", labels: list[str] | None = None, limit: int = 100
    ) -> list[Issue]:
        """List issues with optional filtering."""

    @abstractmethod
    async def list_eligible_issues(self, owner: str, repo: str, label: str = "agent", limit: int = 100) -> list[Issue]:
        """List issues eligible for autonomous work."""

    @abstractmethod
    async def get_issue_comments(self, owner: str, repo: str, number: int) -> list[Comment]:
        """Get all comments on an issue."""

    @abstractmethod
    async def add_label(self, owner: str, repo: str, number: int, label: str) -> bool:
        """Add a label to an issue."""

    @abstractmethod
    async def remove_label(self, owner: str, repo: str, number: int, label: str) -> bool:
        """Remove a label from an issue."""

    @abstractmethod
    async def create_comment(self, owner: str, repo: str, number: int, body: str) -> Comment:
        """Create a comment on an issue."""

    # PR/MR operations
    @abstractmethod
    async def create_pull_request(
        self, owner: str, repo: str, title: str, body: str, head: str, base: str, draft: bool = False
    ) -> PullRequest:
        """Create a pull/merge request."""

    @abstractmethod
    async def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        """Get a pull/merge request by number."""

    @abstractmethod
    async def list_pull_requests(
        self, owner: str, repo: str, state: str = "open", head: str | None = None, base: str | None = None
    ) -> list[PullRequest]:
        """List pull/merge requests."""

    @abstractmethod
    async def update_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
    ) -> PullRequest:
        """Update a pull/merge request."""

    @abstractmethod
    async def merge_pull_request(self, owner: str, repo: str, number: int, method: str = "merge") -> bool:
        """Merge a pull/merge request."""

    # Branch operations
    @abstractmethod
    async def get_branch(self, owner: str, repo: str, branch: str) -> Branch:
        """Get branch information."""

    @abstractmethod
    async def list_branches(self, owner: str, repo: str, limit: int = 100) -> list[Branch]:
        """List branches."""

    @abstractmethod
    async def create_branch(self, owner: str, repo: str, branch: str, from_branch: str | None = None) -> Branch:
        """Create a new branch."""

    # CI/CD operations
    @abstractmethod
    async def get_ci_status(self, owner: str, repo: str, ref: str) -> list[CIStatus]:
        """Get CI/CD status for a commit/ref."""

    # Label operations
    @abstractmethod
    async def get_labels(self, owner: str, repo: str) -> list[Label]:
        """Get all labels for a repository."""

    @abstractmethod
    async def create_label(self, owner: str, repo: str, name: str, color: str, description: str | None = None) -> Label:
        """Create a new label."""

    # Utility
    @abstractmethod
    async def check_rate_limit(self) -> dict[str, Any]:
        """Check current rate limit status."""


def create_provider(provider_type: ProviderType, config: dict[str, Any]) -> GitProvider:
    """Factory function to create provider instances."""
    if provider_type == ProviderType.GITHUB:
        from .github import GitHubProvider

        return GitHubProvider(config)
    elif provider_type == ProviderType.GITLAB:
        from .gitlab import GitLabProvider

        return GitLabProvider(config)
    else:
        raise ValueError(f"Unknown provider type: {provider_type}")
