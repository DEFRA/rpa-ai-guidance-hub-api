import logging
from typing import Annotated

import pydantic
import pydantic_settings

logger = logging.getLogger(__name__)


class BedrockGuardrailConfig(pydantic.BaseModel):
    id: str = pydantic.Field(
        ...,
        pattern=r"^(|([a-z0-9-:.]+)|(arn:aws(-[^:]+)?:bedrock:[a-z0-9-]{1,20}:[0-9]{12}:guardrail/[a-z0-9-:.]+))$",
    )
    version: str = pydantic.Field(..., pattern=r"^(([0-9]{1,8})|(DRAFT))$")


class BedrockModelConfig(pydantic.BaseModel):
    model_id: str
    inference_profile: str = pydantic.Field(
        ...,
        pattern=r"^((arn:aws:bedrock:(|[0-9a-z-]{0,20}):(|[0-9]{12}):(inference-profile|application-inference-profile)/[a-zA-Z0-9-:.]+)|([a-zA-Z0-9-:.]+))$",
    )
    guardrails: BedrockGuardrailConfig | None = None


class BedrockConfig(pydantic.BaseModel):
    claude_sonnet: BedrockModelConfig = pydantic.Field(
        ..., description="Bedrock Claude Sonnet model configuration"
    )


def _parse_bedrock_model_config(v: str) -> BedrockModelConfig:
    """Parse Bedrock model config from string format.

    Expected format: 'model_id,inference_profile[,guardrail_id:guardrail_version]'
    """
    if not isinstance(v, str):
        msg = "Bedrock model config must be a string in the format 'model_id,inference_profile[,guardrail_id:guardrail_version]'"
        raise ValueError(msg)

    s = v.strip()
    parts = [p.strip() for p in s.split(",")]

    if len(parts) < 2:
        msg = "Bedrock model config must have at least model_id and inference_profile"
        raise ValueError(msg)

    (model_id, inference_profile) = parts[0:2]

    guardrails = None

    if len(parts) > 2:
        (guardrail_id, guardrail_version) = parts[2].split(":")

        guardrails = BedrockGuardrailConfig(
            id=guardrail_id,
            version=guardrail_version,
        )

    try:
        return BedrockModelConfig(
            model_id=model_id,
            inference_profile=inference_profile,
            guardrails=guardrails,
        )
    except pydantic.ValidationError as e:
        msg = f"invalid Bedrock model config: {e}"
        raise ValueError(msg) from e


class AppConfig(pydantic_settings.BaseSettings):
    model_config = pydantic_settings.SettingsConfigDict()
    python_env: str | None = None
    host: str = "127.0.0.1"
    port: int = 8085
    log_config: str | None = None
    mongo_uri: str | None = None
    mongo_database: str = "rpa-ai-guidance-hub-api"
    mongo_truststore: str = "TRUSTSTORE_CDP_ROOT_CA"
    floci_endpoint_url: str | None = None
    aws_region: str = pydantic.Field(
        default="eu-west-2", description="AWS region for Bedrock and other services"
    )
    http_proxy: pydantic.HttpUrl | None = None
    enable_metrics: bool = False
    tracing_header: str = "x-cdp-request-id"
    claude_sonnet_model_config: Annotated[
        BedrockModelConfig, pydantic_settings.NoDecode
    ] = pydantic.Field(..., validation_alias="CLAUDE_SONNET_MODEL_CONFIG")
    cdp_uploader_base_url: str = pydantic.Field(
        ..., description="Base URL of the CDP uploader service"
    )
    cdp_uploader_timeout: int = pydantic.Field(
        default=30, description="HTTP timeout for CDP uploader requests"
    )
    callback_base_url: str = pydantic.Field(
        ..., description="Base URL for this service used in callback URLs"
    )
    assets_s3_bucket: str = pydantic.Field(
        ..., description="S3 bucket for evaluation assets"
    )

    @pydantic.field_validator("claude_sonnet_model_config", mode="before")
    @classmethod
    def validate_claude_sonnet_config(
        cls: type[AppConfig], v: str
    ) -> BedrockModelConfig:
        return _parse_bedrock_model_config(v)

    @pydantic.computed_field  # type: ignore[misc]
    def bedrock(self) -> BedrockConfig:
        """Bedrock configuration computed from Claude Sonnet model config."""
        return BedrockConfig(claude_sonnet=self.claude_sonnet_model_config)


_config: AppConfig | None = None


def get_config() -> AppConfig:
    global _config

    if _config is not None:
        return _config

    try:
        _config = AppConfig()  # type: ignore[call-arg]
        return _config
    except pydantic.ValidationError as e:
        error_details = [
            {
                "field": ".".join(str(loc) for loc in error["loc"]),
                "type": error["type"],
                "message": error["msg"],
            }
            for error in e.errors()
        ]

        error_strings = [
            f"Field '{error['field']}' {error['message']}" for error in error_details
        ]

        msg = f"Config validation failed with errors: {', '.join(error_strings)}"
        logger.error(msg)
        raise RuntimeError(msg) from None
