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
os.environ.setdefault("SOURCE_DOCS_S3_BUCKET", "rpa-ai-guidance-hub-source-docs")
os.environ.setdefault(
    "CLAUDE_SONNET_MODEL_CONFIG",
    "anthropic.claude-sonnet-4-6,arn:aws:bedrock:eu-west-2:123456789012:application-inference-profile/fake-profile-test",
)
os.environ.setdefault(
    "REFERENCE_SCHEMES",
    "basic-payment-scheme:Basic Payment Scheme,not-specific:Not scheme-specific",
)
os.environ.setdefault("REFERENCE_AUDIENCES", "caseworker:Caseworker,customer:Customer")
os.environ.setdefault("REFERENCE_SYSTEMS", "siti-agri:Siti Agri,crm:CRM")
os.environ.setdefault("REFERENCE_GUIDANCE_TYPES", "process-guide:Process guide")
