"""Tests for configuration validation."""

import pytest

from orchestrator.config import OrchestratorConfig


class TestProviderValidation:
    def test_default_provider_is_github(self):
        config = OrchestratorConfig()
        assert config.git_provider.provider == "github"

    def test_supported_providers_are_accepted(self):
        for value in ("github", "gitlab"):
            config = OrchestratorConfig.from_dict({"provider": value})
            assert config.git_provider.provider == value

    def test_both_is_rejected(self):
        with pytest.raises(ValueError, match="both"):
            OrchestratorConfig.from_dict({"provider": "both"})

    def test_unknown_provider_is_rejected(self):
        with pytest.raises(ValueError, match="provider"):
            OrchestratorConfig.from_dict({"provider": "bitbucket"})