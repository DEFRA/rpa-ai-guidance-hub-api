from pydantic_ai.models import bedrock as bedrock_models
from pydantic_ai.providers import bedrock as bedrock_providers

from app import config

settings = config.get_config()

provider = bedrock_providers.BedrockProvider(region_name=settings.aws_region)


def _setup_model(
    model_config: config.BedrockModelConfig,
) -> bedrock_models.BedrockConverseModel:
    """Create a BedrockConverseModel from configuration."""

    def build_settings() -> bedrock_models.BedrockModelSettings:
        settings = bedrock_models.BedrockModelSettings(
            bedrock_inference_profile=model_config.inference_profile,
            temperature=0.0,
        )

        if model_config.guardrails:
            settings["bedrock_guardrail_config"] = {
                "guardrailIdentifier": model_config.guardrails.id,
                "guardrailVersion": model_config.guardrails.version,
                "trace": "enabled",
            }

        return settings

    model_name = model_config.model_id

    return bedrock_models.BedrockConverseModel(
        model_name,
        provider=provider,
        settings=build_settings(),
    )


claude_sonnet = _setup_model(settings.bedrock.claude_sonnet)  # type: ignore[attr-defined]
