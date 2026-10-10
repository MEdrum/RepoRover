"""
Orchestrator Configuration
"""

import os
from dataclasses import dataclass, field

DEFAULT_LABELS = {
    "eligible": "agent",
    "in_progress": "doing",
    "stuck": "stuck",
    "awaiting_review": "awaiting review",
}


@dataclass
class GitProviderConfig:
    """Git provider configuration."""

    provider: str = "github"  # github, gitlab, both
    github_token: str = ""
    github_base_url: str = "https://api.github.com"
    gitlab_token: str = ""
    gitlab_url: str = "https://gitlab.com"
    labels: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_LABELS))
    repositories: list[str] = field(default_factory=list)
    polling_interval: int = 60
    max_issues_per_poll: int = 50


@dataclass
class OpenCodeConfig:
    """OpenCode service configuration."""

    api_url: str = "http://opencode:8080"
    password: str = ""
    timeout: int = 300
    # max_concurrent is reserved for the upcoming concurrency feature; it is
    # not yet used by the runtime.
    max_concurrent: int = 1


@dataclass
class StateConfig:
    """State persistence configuration."""

    state_dir: str = "/data/state"
    max_history: int = 1000


@dataclass
class RetryConfig:
    """Retry/backoff configuration for workflow phases."""

    max_attempts: int = 3
    base_delay_seconds: int = 2
    max_delay_seconds: int = 60
    exponential_base: int = 2


@dataclass
class OrchestratorConfig:
    """Main orchestrator configuration."""

    git_provider: GitProviderConfig = field(default_factory=GitProviderConfig)
    opencode: OpenCodeConfig = field(default_factory=OpenCodeConfig)
    state: StateConfig = field(default_factory=StateConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    log_level: str = "INFO"

    @classmethod
    def from_dict(cls, data: dict) -> "OrchestratorConfig":
        """Create config from a (possibly nested) dictionary.

        Dataclass field defaults are the single source of truth; values in
        `data` override them only when present.
        """
        config = cls()
        config.git_provider = _git_provider_from_dict(data.get("git_provider", data))
        config.opencode = _opencode_from_dict(data.get("opencode", {}))
        config.state = _state_from_dict(data.get("state", {}))
        config.retry = _retry_from_dict(data.get("retry", {}))
        config.log_level = data.get("log_level", config.log_level)
        config.validate()
        return config

    def validate(self) -> None:
        """Validate the configuration and fail fast with actionable errors."""
        gp = self.git_provider

        if gp.provider not in ("github", "gitlab", "both"):
            raise ValueError(
                f"Unsupported git provider: {gp.provider!r}. Supported values: 'github', 'gitlab', 'both'."
            )
        if gp.provider in ("github", "both") and not gp.github_token:
            raise ValueError("git provider requires a GitHub token (config github.token or GITHUB_TOKEN)")
        if gp.provider in ("gitlab", "both") and not gp.gitlab_token:
            raise ValueError("git provider requires a GitLab token (config gitlab.token or GITLAB_TOKEN)")

        for repo in gp.repositories:
            owner, _, name = repo.partition("/")
            if not owner or not name:
                raise ValueError(f"Invalid repository spec: {repo!r}. Expected 'owner/repo'.")

        if gp.polling_interval <= 0:
            raise ValueError(f"polling.interval_seconds must be positive, got {gp.polling_interval}")
        if gp.max_issues_per_poll <= 0:
            raise ValueError(f"polling.max_issues_per_poll must be positive, got {gp.max_issues_per_poll}")

        for key, label in gp.labels.items():
            if not isinstance(label, str) or not label.strip():
                raise ValueError(f"labels.{key} must be a non-empty string, got {label!r}")

        if not self.opencode.api_url:
            raise ValueError("opencode.api_url must not be empty")
        if self.opencode.timeout <= 0:
            raise ValueError(f"opencode.timeout must be positive, got {self.opencode.timeout}")

        if self.retry.max_attempts < 1:
            raise ValueError(f"retry.max_attempts must be at least 1, got {self.retry.max_attempts}")
        if self.retry.base_delay_seconds < 0 or self.retry.max_delay_seconds < 0:
            raise ValueError("retry delays must not be negative")
        if self.retry.exponential_base < 1:
            raise ValueError(f"retry.exponential_base must be at least 1, got {self.retry.exponential_base}")

        if not self.state.state_dir.strip():
            raise ValueError("state.state_dir must not be empty")


def _git_provider_from_dict(data: dict) -> GitProviderConfig:
    defaults = GitProviderConfig()
    github = data.get("github", {}) or {}
    gitlab = data.get("gitlab", {}) or {}
    polling = data.get("polling", {}) or {}
    return GitProviderConfig(
        provider=data.get("provider", defaults.provider),
        github_token=github.get("token") or os.environ.get("GITHUB_TOKEN", "") or defaults.github_token,
        github_base_url=github.get("base_url", defaults.github_base_url),
        gitlab_token=gitlab.get("token") or os.environ.get("GITLAB_TOKEN", "") or defaults.gitlab_token,
        gitlab_url=gitlab.get("url", defaults.gitlab_url),
        labels=data.get("labels", defaults.labels),
        repositories=data.get("repositories", defaults.repositories),
        polling_interval=polling.get("interval_seconds", defaults.polling_interval),
        max_issues_per_poll=polling.get("max_issues_per_poll", defaults.max_issues_per_poll),
    )


def _opencode_from_dict(data: dict) -> OpenCodeConfig:
    defaults = OpenCodeConfig()
    return OpenCodeConfig(
        api_url=data.get("api_url", defaults.api_url),
        password=data.get("password") or os.environ.get("OPENCODE_SERVER_PASSWORD", "") or defaults.password,
        timeout=data.get("timeout", defaults.timeout),
        max_concurrent=data.get("max_concurrent", defaults.max_concurrent),
    )


def _state_from_dict(data: dict) -> StateConfig:
    defaults = StateConfig()
    return StateConfig(
        state_dir=data.get("state_dir", defaults.state_dir),
        max_history=data.get("max_history", defaults.max_history),
    )


def _retry_from_dict(data: dict) -> RetryConfig:
    defaults = RetryConfig()
    return RetryConfig(
        max_attempts=data.get("max_attempts", defaults.max_attempts),
        base_delay_seconds=data.get("base_delay_seconds", defaults.base_delay_seconds),
        max_delay_seconds=data.get("max_delay_seconds", defaults.max_delay_seconds),
        exponential_base=data.get("exponential_base", defaults.exponential_base),
    )
