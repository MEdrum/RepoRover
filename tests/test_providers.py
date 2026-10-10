"""Tests for the provider manager and repository routing."""

import asyncio
from unittest import mock

import pytest

from git_provider import NotFoundError, ProviderType
from orchestrator.config import OrchestratorConfig
from orchestrator.providers import ProviderManager


class FakeGitHubProvider:
    def __init__(self, repos=()):
        self.repos = set(repos)
        self.get_repository_calls = 0

    async def authenticate(self):
        return True

    async def get_repository(self, owner, repo):
        self.get_repository_calls += 1
        if f"{owner}/{repo}" in self.repos:
            return object()
        raise NotFoundError("not found")


class FakeGitLabProvider:
    def __init__(self, repos=()):
        self.repos = set(repos)
        self.get_repository_calls = 0

    async def authenticate(self):
        return True

    async def get_repository(self, owner, repo):
        self.get_repository_calls += 1
        if f"{owner}/{repo}" in self.repos:
            return object()
        raise NotFoundError("not found")


def _manager(provider="both", github_repos=(), gitlab_repos=()):
    config = OrchestratorConfig()
    config.git_provider.provider = provider
    config.git_provider.github_token = "gh"
    config.git_provider.gitlab_token = "gl"
    manager = ProviderManager(config)
    github = FakeGitHubProvider(github_repos)
    gitlab = FakeGitLabProvider(gitlab_repos)

    def _create(provider_type, cfg):
        if provider_type == ProviderType.GITHUB:
            return github
        return gitlab

    return manager, github, gitlab, _create


class TestProviderRouting:
    def test_both_routes_github_repo_to_github(self):
        manager, github, _, create = _manager(github_repos=("ghorg/repo",))
        with mock.patch("git_provider.create_provider", side_effect=create):
            provider = asyncio.run(manager.provider_for("ghorg/repo"))
        assert provider is github

    def test_both_routes_gitlab_repo_to_gitlab(self):
        manager, _, gitlab, create = _manager(gitlab_repos=("glorg/repo",))
        with mock.patch("git_provider.create_provider", side_effect=create):
            provider = asyncio.run(manager.provider_for("glorg/repo"))
        assert provider is gitlab

    def test_routing_is_cached_per_repository(self):
        manager, github, gitlab, create = _manager(github_repos=("ghorg/repo",))
        with mock.patch("git_provider.create_provider", side_effect=create):
            asyncio.run(manager.provider_for("ghorg/repo"))
            asyncio.run(manager.provider_for("ghorg/repo"))
        assert github.get_repository_calls == 1
        assert gitlab.get_repository_calls == 0

    def test_repo_on_neither_provider_raises(self):
        manager, _, _, create = _manager()
        with mock.patch("git_provider.create_provider", side_effect=create), pytest.raises(NotFoundError):
            asyncio.run(manager.provider_for("nowhere/repo"))

    def test_single_provider_returns_provider_directly(self):
        manager, github, _, create = _manager(provider="github")
        with mock.patch("git_provider.create_provider", side_effect=create):
            provider = asyncio.run(manager.provider_for("any/repo"))
        assert provider is github
        assert github.get_repository_calls == 0
