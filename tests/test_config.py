"""Tests for configuration validation."""

import pytest

from orchestrator.config import OrchestratorConfig


def _both_config(github_token="gh", gitlab_token="gl"):
    return OrchestratorConfig.from_dict({
        "provider": "both",
        "github": {"token": github_token},
        "gitlab": {"token": gitlab_token},
    })


class TestProviderValidation:
    def test_default_provider_is_github(self):
        config = OrchestratorConfig()
        assert config.git_provider.provider == "github"

    def test_supported_providers_are_accepted(self):
        for value in ("github", "gitlab"):
            config = OrchestratorConfig.from_dict({"provider": value})
            assert config.git_provider.provider == value

    def test_both_is_accepted_with_both_tokens(self):
        config = _both_config()
        assert config.git_provider.provider == "both"
        assert config.git_provider.github_token == "gh"
        assert config.git_provider.gitlab_token == "gl"

    def test_both_requires_github_token(self):
        with pytest.raises(ValueError, match="GitHub"):
            _both_config(github_token="")

    def test_both_requires_gitlab_token(self):
        with pytest.raises(ValueError, match="GitLab"):
            _both_config(gitlab_token="")

    def test_unknown_provider_is_rejected(self):
        with pytest.raises(ValueError, match="provider"):
            OrchestratorConfig.from_dict({"provider": "bitbucket"})