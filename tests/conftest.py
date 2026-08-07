import os

# AppConfig environment defaults (use the same names pydantic-settings will look up)
os.environ.setdefault("PYTHON_ENV", "test")
os.environ.setdefault("AWS_REGION", "eu-west-2")
os.environ.setdefault("HOST", "127.0.0.1")
os.environ.setdefault("PORT", "8085")
os.environ.setdefault("LOG_CONFIG", "logging-dev.json")
os.environ.setdefault("MONGO_URI", "mongodb://localhost:27017")
os.environ.setdefault("MONGO_DATABASE", "rpa-ai-guidance-hub-api")
os.environ.setdefault("MONGO_TRUSTSTORE", "TRUSTSTORE_CDP_ROOT_CA")
os.environ.setdefault("ENABLE_METRICS", "false")
os.environ.setdefault(
    "CLAUDE_SONNET_MODEL_CONFIG",
    "anthropic.claude-sonnet-4-6,arn:aws:bedrock:eu-west-2:123456789012:application-inference-profile/fake-profile-test",
)
os.environ.setdefault("CDP_UPLOADER_BASE_URL", "http://localhost:8087")
os.environ.setdefault("CDP_UPLOADER_TIMEOUT", "30")
os.environ.setdefault("CALLBACK_BASE_URL", "http://localhost:8085")
os.environ.setdefault("ASSETS_S3_BUCKET", "rpa-ai-guidance-hub-assets")
