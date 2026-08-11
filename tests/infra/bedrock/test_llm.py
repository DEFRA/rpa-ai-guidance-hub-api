"""Tests for the Bedrock LLM module."""

import importlib

import pytest

from app.infra.bedrock import llm


class TestClaudeSonnet:
    """Test claude_sonnet module-level export."""

    @pytest.fixture(autouse=True)
    def restore_module(self):
        yield
        importlib.reload(llm)

    def test_created_without_guardrails(self) -> None:
        assert "bedrock_guardrail_config" not in llm.claude_sonnet.settings

    def test_created_with_guardrails(self, mocker) -> None:
        mock_config = mocker.Mock()
        mock_config.aws_region = "eu-west-2"
        mock_config.bedrock.claude_sonnet.model_id = "anthropic.claude-sonnet-4-6"
        mock_config.bedrock.claude_sonnet.inference_profile = (
            "arn:aws:bedrock:eu-west-2:123456789012:application-inference-profile/test"
        )
        mock_config.bedrock.claude_sonnet.guardrails.id = "guardrail-123"
        mock_config.bedrock.claude_sonnet.guardrails.version = "1"

        mocker.patch("app.config.get_config", return_value=mock_config)
        importlib.reload(llm)

        assert llm.claude_sonnet.settings["bedrock_guardrail_config"] == {
            "guardrailIdentifier": "guardrail-123",
            "guardrailVersion": "1",
            "trace": "enabled",
        }
