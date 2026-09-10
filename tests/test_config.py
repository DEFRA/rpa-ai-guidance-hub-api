"""Tests for application configuration parsing and the config singleton."""

import logging

import pydantic
import pytest

from app import config

VALID_PROFILE = (
    "arn:aws:bedrock:eu-west-2:123456789012:application-inference-profile/test-profile"
)
VALID_MODEL = "anthropic.claude-sonnet-4-6"

# The four reference-data fields are all required, so every direct AppConfig(...)
# construction below needs these too - spread this in alongside CLAUDE_SONNET_MODEL_CONFIG.
VALID_REFERENCE_KWARGS = {
    "REFERENCE_SCHEMES": "basic-payment-scheme:Basic Payment Scheme",
    "REFERENCE_AUDIENCES": "caseworker:Caseworker",
    "REFERENCE_SYSTEMS": "siti-agri:Siti Agri",
    "REFERENCE_GUIDANCE_TYPES": "process-guide:Process guide",
}

# One id per branch of the BedrockGuardrailConfig.id pattern, including the
# colon-bearing forms the pattern permits.
GUARDRAIL_IDS = [
    "",
    "pii-filter",
    "pii.filter-2",
    "foo:bar",
    "a:b:c:d",
    "arn:aws:bedrock:eu-west-2:123456789012:guardrail/pii",
    "arn:aws-cn:bedrock:cn-north-1:123456789012:guardrail/pii",
]


@pytest.fixture
def unset_config_singleton():
    """Clear the cached AppConfig so get_config() rebuilds, then restore it."""
    original = config._config
    config._config = None
    yield
    config._config = original


class TestParseBedrockModelConfig:
    """The packed 'model_id,inference_profile[,guardrail_id:version]' string."""

    def test_parses_model_id_and_inference_profile(self):
        parsed = config._parse_bedrock_model_config(f"{VALID_MODEL},{VALID_PROFILE}")

        assert parsed.model_id == VALID_MODEL
        assert parsed.inference_profile == VALID_PROFILE
        assert parsed.guardrails is None

    def test_strips_whitespace_around_each_part(self):
        parsed = config._parse_bedrock_model_config(
            f"  {VALID_MODEL} , {VALID_PROFILE}  "
        )

        assert parsed.model_id == VALID_MODEL
        assert parsed.inference_profile == VALID_PROFILE

    @pytest.mark.parametrize("guardrail_id", GUARDRAIL_IDS)
    @pytest.mark.parametrize("guardrail_version", ["1", "12345678", "DRAFT"])
    def test_round_trips_every_id_the_model_accepts(
        self, guardrail_id, guardrail_version
    ):
        """The parser must not be stricter than BedrockGuardrailConfig itself.

        An id may contain colons - both the short-id character class and the ARN
        form permit them - while a version never can, so the packed value has to
        split on the last colon rather than the first.
        """
        config.BedrockGuardrailConfig(id=guardrail_id, version=guardrail_version)

        parsed = config._parse_bedrock_model_config(
            f"{VALID_MODEL},{VALID_PROFILE},{guardrail_id}:{guardrail_version}"
        )

        assert parsed.guardrails is not None
        assert parsed.guardrails.id == guardrail_id
        assert parsed.guardrails.version == guardrail_version

    def test_rejects_non_string_input(self):
        with pytest.raises(ValueError, match="must be a string"):
            config._parse_bedrock_model_config(1234)

    @pytest.mark.parametrize("value", ["", "only-a-model-id"])
    def test_rejects_fewer_than_two_parts(self, value):
        with pytest.raises(ValueError, match="at least model_id and inference_profile"):
            config._parse_bedrock_model_config(value)

    def test_wraps_model_validation_failure(self):
        # Underscores fall outside the inference-profile pattern.
        with pytest.raises(ValueError, match="invalid Bedrock model config"):
            config._parse_bedrock_model_config(f"{VALID_MODEL},bad_profile")

    def test_rejects_invalid_guardrail_version(self):
        with pytest.raises(pydantic.ValidationError):
            config._parse_bedrock_model_config(
                f"{VALID_MODEL},{VALID_PROFILE},my-guardrail:not-a-version"
            )

    def test_rejects_guardrail_without_a_version(self):
        with pytest.raises(ValueError, match="guardrail_id:guardrail_version"):
            config._parse_bedrock_model_config(
                f"{VALID_MODEL},{VALID_PROFILE},no-colon-here"
            )


class TestParseReferenceOptions:
    """The packed 'value1:Label One,value2:Label Two' string."""

    def test_parses_an_ordered_list_of_value_label_pairs(self):
        parsed = config._parse_reference_options(
            "basic-payment-scheme:Basic Payment Scheme,not-specific:Not scheme-specific"
        )

        assert parsed == [
            {"value": "basic-payment-scheme", "label": "Basic Payment Scheme"},
            {"value": "not-specific", "label": "Not scheme-specific"},
        ]

    def test_strips_whitespace_around_each_part(self):
        parsed = config._parse_reference_options("  siti-agri : Siti Agri  ")

        assert parsed == [{"value": "siti-agri", "label": "Siti Agri"}]

    def test_rejects_non_string_input(self):
        with pytest.raises(ValueError, match="must be a string"):
            config._parse_reference_options(1234)

    def test_rejects_an_empty_string(self):
        with pytest.raises(ValueError, match="at least one entry"):
            config._parse_reference_options("")

    def test_rejects_an_entry_missing_its_colon(self):
        with pytest.raises(ValueError, match="expected 'value:label'"):
            config._parse_reference_options("no-colon-here")

    @pytest.mark.parametrize("entry", [":Label With No Value", "value-with-no-label:"])
    def test_rejects_an_entry_with_an_empty_value_or_label(self, entry):
        with pytest.raises(ValueError, match="expected 'value:label'"):
            config._parse_reference_options(entry)

    def test_one_malformed_entry_fails_the_whole_value(self):
        """Fail-fast, not partial recovery - a bad entry never silently vanishes."""
        with pytest.raises(ValueError, match="expected 'value:label'"):
            config._parse_reference_options("siti-agri:Siti Agri,no-colon-here,crm:CRM")


class TestAppConfig:
    """Field defaults and the derived Bedrock configuration."""

    def test_defaults(self):
        cfg = config.AppConfig(
            CLAUDE_SONNET_MODEL_CONFIG=f"{VALID_MODEL},{VALID_PROFILE}",
            **VALID_REFERENCE_KWARGS,
        )

        assert cfg.host == "127.0.0.1"
        assert cfg.port == 8085
        assert cfg.mongo_database == "rpa-ai-guidance-hub-api"
        assert cfg.mongo_truststore == "TRUSTSTORE_CDP_ROOT_CA"
        assert cfg.aws_region == "eu-west-2"
        assert cfg.tracing_header == "x-cdp-request-id"
        assert cfg.enable_metrics is False
        assert cfg.floci_endpoint_url is None
        # Required, so read back from the env default conftest.py seeds, not a
        # field default - source_docs_s3_bucket has none.
        assert cfg.source_docs_s3_bucket == "rpa-ai-guidance-hub-source-docs"
        assert cfg.http_proxy is None

    def test_validator_parses_the_packed_model_config(self):
        cfg = config.AppConfig(
            CLAUDE_SONNET_MODEL_CONFIG=f"{VALID_MODEL},{VALID_PROFILE}",
            **VALID_REFERENCE_KWARGS,
        )

        assert cfg.claude_sonnet_model_config.model_id == VALID_MODEL

    def test_bedrock_exposes_the_claude_sonnet_model(self):
        cfg = config.AppConfig(
            CLAUDE_SONNET_MODEL_CONFIG=f"{VALID_MODEL},{VALID_PROFILE}",
            **VALID_REFERENCE_KWARGS,
        )

        assert cfg.bedrock.claude_sonnet.model_id == VALID_MODEL
        assert cfg.bedrock.claude_sonnet.inference_profile == VALID_PROFILE

    def test_validator_parses_each_reference_option_list(self):
        cfg = config.AppConfig(
            CLAUDE_SONNET_MODEL_CONFIG=f"{VALID_MODEL},{VALID_PROFILE}",
            **VALID_REFERENCE_KWARGS,
        )

        assert cfg.reference_schemes[0] == {
            "value": "basic-payment-scheme",
            "label": "Basic Payment Scheme",
        }
        assert cfg.reference_audiences[0] == {
            "value": "caseworker",
            "label": "Caseworker",
        }
        assert cfg.reference_systems[0] == {"value": "siti-agri", "label": "Siti Agri"}
        assert cfg.reference_guidance_types[0] == {
            "value": "process-guide",
            "label": "Process guide",
        }


@pytest.mark.usefixtures("unset_config_singleton")
class TestGetConfig:
    """The validate-on-construct singleton."""

    def test_builds_and_caches_a_single_instance(self):
        first = config.get_config()
        second = config.get_config()

        assert isinstance(first, config.AppConfig)
        assert first is second

    def test_returns_the_cached_instance_without_rebuilding(self, mocker):
        cached = config.get_config()
        constructor = mocker.patch.object(config, "AppConfig")

        assert config.get_config() is cached
        constructor.assert_not_called()

    def test_raises_runtime_error_naming_the_offending_variable(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_SONNET_MODEL_CONFIG", raising=False)

        with pytest.raises(RuntimeError, match="Config validation failed") as exc_info:
            config.get_config()

        # The validation alias is reported, so the message names the env var an
        # operator has to set rather than the internal field name.
        assert "CLAUDE_SONNET_MODEL_CONFIG" in str(exc_info.value)
        assert "Field required" in str(exc_info.value)

    def test_logs_the_validation_failure(self, monkeypatch, caplog):
        monkeypatch.delenv("CLAUDE_SONNET_MODEL_CONFIG", raising=False)

        with (
            caplog.at_level(logging.ERROR, logger="app.config"),
            pytest.raises(RuntimeError),
        ):
            config.get_config()

        assert "Config validation failed" in caplog.text

    def test_does_not_cache_a_failed_construction(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_SONNET_MODEL_CONFIG", raising=False)

        with pytest.raises(RuntimeError):
            config.get_config()

        assert config._config is None

    def test_raises_when_a_reference_option_list_is_missing(self, monkeypatch):
        monkeypatch.delenv("REFERENCE_SCHEMES", raising=False)

        with pytest.raises(RuntimeError, match="Config validation failed") as exc_info:
            config.get_config()

        assert "REFERENCE_SCHEMES" in str(exc_info.value)
        assert "Field required" in str(exc_info.value)

    def test_raises_when_a_reference_option_list_is_malformed(self, monkeypatch):
        monkeypatch.setenv("REFERENCE_SCHEMES", "no-colon-here")

        with pytest.raises(RuntimeError, match="Config validation failed") as exc_info:
            config.get_config()

        assert "REFERENCE_SCHEMES" in str(exc_info.value)
