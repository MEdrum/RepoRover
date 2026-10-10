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


class TestConfigDefaultsAndSecrets:
    def test_state_dir_defaults_to_data_state(self):
        config = OrchestratorConfig.from_dict({})
        assert config.state.state_dir == "/data/state"

    def test_state_dir_can_be_overridden(self):
        config = OrchestratorConfig.from_dict({"state": {"state_dir": "/custom"}})
        assert config.state.state_dir == "/custom"

    def test_opencode_password_from_dict(self):
        config = OrchestratorConfig.from_dict({"opencode": {"password": "secret"}})
        assert config.opencode.password == "secret"

    def test_opencode_password_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "env-secret")
        config = OrchestratorConfig.from_dict({})
        assert config.opencode.password == "env-secret"

    def test_removed_memory_and_rate_limit_keys_are_tolerated(self):
        config = OrchestratorConfig.from_dict({
            "memory": {"enabled": True, "api_url": "http://x"},
            "rate_limit": {"github": {"requests_per_hour": 5000}},
        })
        assert not hasattr(config, "memory")
        assert not hasattr(config.git_provider, "rate_limit")