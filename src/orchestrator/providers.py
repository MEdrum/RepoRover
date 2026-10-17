"""
Provider Manager

Manages one or more Git provider adapters and routes repositories to the
provider that owns them. Supports running GitHub and GitLab side by side
(`provider: both`) as well as a single provider.
"""

import logging

import git_provider
from git_provider import (
    GitProvider,
    NotFoundError,
    ProviderType,
)
from orchestrator.config import OrchestratorConfig

logger = logging.getLogger(__name__)


class ProviderManager:
    """Owns provider adapters and resolves the provider for a repository."""

    def __init__(self, config: OrchestratorConfig):
        """Own provider adapters and cache per-repository routing."""
        self._git_provider_config = config.git_provider
        self._providers: dict[ProviderType, GitProvider] = {}
        self._repo_provider: dict[str, ProviderType] = {}

    def active_types(self) -> list[ProviderType]:
        """The provider types this configuration should use."""
        if self._git_provider_config.provider == "both":
            return [ProviderType.GITHUB, ProviderType.GITLAB]
        if self._git_provider_config.provider == "gitlab":
            return [ProviderType.GITLAB]
        return [ProviderType.GITHUB]

    def _provider_config(self, provider_type: ProviderType) -> dict:
        """Return the config dict passed to create_provider for a provider type."""
        if provider_type == ProviderType.GITHUB:
            return {
                "token": self._git_provider_config.github_token,
                "base_url": self._git_provider_config.github_base_url,
            }
        return {
            "token": self._git_provider_config.gitlab_token,
            "url": self._git_provider_config.gitlab_url,
        }

    async def _ensure_provider(self, provider_type: ProviderType) -> GitProvider:
        """Create and authenticate a provider if not already cached."""
        if provider_type not in self._providers:
            provider = git_provider.create_provider(provider_type, self._provider_config(provider_type))
            await provider.authenticate()
            self._providers[provider_type] = provider
        return self._providers[provider_type]

    async def provider_for(self, repository: str) -> GitProvider:
        """Resolve the provider that owns a repository.

        With a single configured provider it is returned directly. With
        `provider: both` the repository is probed on GitHub first and then on
        GitLab; the first provider that resolves it owns it. The mapping is
        cached for the lifetime of the manager.
        """
        cached = self._repo_provider.get(repository)
        if cached is not None:
            return await self._ensure_provider(cached)
        if self._git_provider_config.provider != "both":
            return await self._ensure_provider(self.active_types()[0])
        owner, repo = repository.split("/", 1)
        for provider_type in self.active_types():
            provider = await self._ensure_provider(provider_type)
            try:
                await provider.get_repository(owner, repo)
            except NotFoundError:
                continue
            self._repo_provider[repository] = provider_type
            return provider
        raise NotFoundError(f"Repository {repository} not found on any configured provider")
