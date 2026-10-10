"""Tests for configuration validation."""

import pytest

from orchestrator.config import OrchestratorConfig


def _both_config(github_token="gh", gitlab_token="gl"):
    return OrchestratorConfig.from_dict(
        {
            "provider": "both",
            "github": {"token": github_token},
            "gitlab": {"token": gitlab_token},
        }
    )


class TestProviderValidation:
    def test_default_provider_is_github(self):
        config = OrchestratorConfig()
        assert config.git_provider.provider == "github"

    def test_supported_providers_are_accepted(self):
        config = OrchestratorConfig.from_dict({"provider": "github", "github": {"token": "t"}})
        assert config.git_provider.provider == "github"
        config = OrchestratorConfig.from_dict({"provider": "gitlab", "gitlab": {"token": "t"}})
        assert config.git_provider.provider == "gitlab"

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
        config = OrchestratorConfig.from_dict({"github": {"token": "t"}})
        assert config.state.state_dir == "/data/state"

    def test_state_dir_can_be_overridden(self):
        config = OrchestratorConfig.from_dict(
            {
                "github": {"token": "t"},
                "state": {"state_dir": "/custom"},
            }
        )
        assert config.state.state_dir == "/custom"

    def test_opencode_password_from_dict(self):
        config = OrchestratorConfig.from_dict(
            {
                "github": {"token": "t"},
                "opencode": {"password": "secret"},
            }
        )
        assert config.opencode.password == "secret"

    def test_opencode_password_from_env(self, monkeypatch):
        monkeypatch.setenv("OPENCODE_SERVER_PASSWORD", "env-secret")
        config = OrchestratorConfig.from_dict({"github": {"token": "t"}})
        assert config.opencode.password == "env-secret"

    def test_removed_memory_and_rate_limit_keys_are_tolerated(self):
        config = OrchestratorConfig.from_dict(
            {
                "github": {"token": "t"},
                "memory": {"enabled": True, "api_url": "http://x"},
                "rate_limit": {"github": {"requests_per_hour": 5000}},
            }
        )
        assert not hasattr(config, "memory")
        assert not hasattr(config.git_provider, "rate_limit")

    def test_discover_all_repositories_flag(self):
        config = OrchestratorConfig.from_dict({"github": {"token": "t"}})
        assert config.git_provider.discover_all_repositories is False
        config = OrchestratorConfig.from_dict(
            {
                "github": {"token": "t"},
                "discover_all_repositories": True,
            }
        )
        assert config.git_provider.discover_all_repositories is True


class TestValidation:
    def test_invalid_repository_syntax_is_rejected(self):
        with pytest.raises(ValueError, match="owner/repo"):
            OrchestratorConfig.from_dict(
                {
                    "github": {"token": "t"},
                    "repositories": ["not-a-repo"],
                }
            )

    def test_non_positive_polling_interval_is_rejected(self):
        with pytest.raises(ValueError, match="interval_seconds"):
            OrchestratorConfig.from_dict(
                {
                    "github": {"token": "t"},
                    "polling": {"interval_seconds": 0},
                }
            )

    def test_empty_label_value_is_rejected(self):
        with pytest.raises(ValueError, match=r"labels\.eligible"):
            OrchestratorConfig.from_dict(
                {
                    "github": {"token": "t"},
                    "labels": {"eligible": "  "},
                }
            )

    def test_non_positive_opencode_timeout_is_rejected(self):
        with pytest.raises(ValueError, match="timeout"):
            OrchestratorConfig.from_dict(
                {
                    "github": {"token": "t"},
                    "opencode": {"timeout": -1},
                }
            )

    def test_invalid_retry_attempts_are_rejected(self):
        with pytest.raises(ValueError, match="max_attempts"):
            OrchestratorConfig.from_dict(
                {
                    "github": {"token": "t"},
                    "retry": {"max_attempts": 0},
                }
            )

    def test_missing_active_provider_token_is_rejected(self):
        with pytest.raises(ValueError, match="GitHub"):
            OrchestratorConfig.from_dict({"provider": "github", "github": {"token": ""}})
