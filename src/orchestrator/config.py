"""
Orchestrator Configuration
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Any
import yaml


@dataclass
class GitProviderConfig:
    """Git provider configuration."""
    provider: str = "github"  # github, gitlab, both
    github_token: str = ""
    github_base_url: str = "https://api.github.com"
    gitlab_token: str = ""
    gitlab_url: str = "https://gitlab.com"
    labels: Dict[str, str] = field(default_factory=lambda: {
        "eligible": "agent",
        "in_progress": "doing",
        "stuck": "stuck",
        "awaiting_review": "awaiting review"
    })
    repositories: List[str] = field(default_factory=list)
    rate_limit: Dict[str, Any] = field(default_factory=dict)
    polling_interval: int = 60
    max_issues_per_poll: int = 50


@dataclass
class OpenCodeConfig:
    """OpenCode configuration."""
    api_url: str = "http://opencode:8080"
    timeout: int = 300
    max_concurrent: int = 1


@dataclass
class MemoryConfig:
    """Memory service configuration."""
    api_url: str = "http://agent-memory:8000/api/v1"
    enabled: bool = True


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
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    state: StateConfig = field(default_factory=StateConfig)
    retry: RetryConfig = field(default_factory=RetryConfig)
    log_level: str = "INFO"
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "OrchestratorConfig":
        """Create config from dictionary."""
        config = cls()
        
        # Git provider config
        gp_data = data.get("git_provider", data)
        config.git_provider = GitProviderConfig(
            provider=gp_data.get("provider", "github"),
            github_token=gp_data.get("github", {}).get("token", "") or os.environ.get("GITHUB_TOKEN", ""),
            github_base_url=gp_data.get("github", {}).get("base_url", "https://api.github.com"),
            gitlab_token=gp_data.get("gitlab", {}).get("token", "") or os.environ.get("GITLAB_TOKEN", ""),
            gitlab_url=gp_data.get("gitlab", {}).get("url", "https://gitlab.com"),
            labels=gp_data.get("labels", {
                "eligible": "agent",
                "in_progress": "doing",
                "stuck": "stuck",
                "awaiting_review": "awaiting review"
            }),
            repositories=gp_data.get("repositories", []),
            rate_limit=gp_data.get("rate_limit", {}),
            polling_interval=gp_data.get("polling", {}).get("interval_seconds", 60),
            max_issues_per_poll=gp_data.get("polling", {}).get("max_issues_per_poll", 50)
        )
        
        # OpenCode config
        oc_data = data.get("opencode", {})
        config.opencode = OpenCodeConfig(
            api_url=oc_data.get("api_url", "http://opencode:8080"),
            timeout=oc_data.get("timeout", 300),
            max_concurrent=oc_data.get("max_concurrent", 1)
        )
        
        # Memory config
        mem_data = data.get("memory", {})
        config.memory = MemoryConfig(
            api_url=mem_data.get("api_url", "http://agent-memory:8000/api/v1"),
            enabled=mem_data.get("enabled", True)
        )
        
        # State config
        state_data = data.get("state", {})
        config.state = StateConfig(
            state_dir=state_data.get("state_dir", "/home/fabian/docker/storage/agent-orchestrator/state"),
            max_history=state_data.get("max_history", 1000)
        )
        
        # Retry config
        retry_data = data.get("retry", {})
        config.retry = RetryConfig(
            max_attempts=retry_data.get("max_attempts", 3),
            base_delay_seconds=retry_data.get("base_delay_seconds", 2),
            max_delay_seconds=retry_data.get("max_delay_seconds", 60),
            exponential_base=retry_data.get("exponential_base", 2)
        )
        
        config.log_level = data.get("log_level", "INFO")
        
        return config
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "git_provider": {
                "provider": self.git_provider.provider,
                "github": {
                    "token": self.git_provider.github_token,
                    "base_url": self.git_provider.github_base_url
                },
                "gitlab": {
                    "token": self.git_provider.gitlab_token,
                    "url": self.git_provider.gitlab_url
                },
                "labels": self.git_provider.labels,
                "repositories": self.git_provider.repositories,
                "rate_limit": self.git_provider.rate_limit,
                "polling": {
                    "interval_seconds": self.git_provider.polling_interval,
                    "max_issues_per_poll": self.git_provider.max_issues_per_poll
                }
            },
            "opencode": {
                "api_url": self.opencode.api_url,
                "timeout": self.opencode.timeout,
                "max_concurrent": self.opencode.max_concurrent
            },
            "memory": {
                "api_url": self.memory.api_url,
                "enabled": self.memory.enabled
            },
            "state": {
                "state_dir": self.state.state_dir,
                "max_history": self.state.max_history
            },
            "retry": {
                "max_attempts": self.retry.max_attempts,
                "base_delay_seconds": self.retry.base_delay_seconds,
                "max_delay_seconds": self.retry.max_delay_seconds,
                "exponential_base": self.retry.exponential_base
            },
            "log_level": self.log_level
        }