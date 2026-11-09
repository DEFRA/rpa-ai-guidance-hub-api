"""Whether the API has AWS credentials to start with."""

import pytest

from app.common import aws

# Where boto3 finds credentials in the environment; the files and the instance's own
# are pointed elsewhere by the fixture.
_CREDENTIAL_VARIABLES = (
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_PROFILE",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_CONTAINER_CREDENTIALS_FULL_URI",
    "AWS_WEB_IDENTITY_TOKEN_FILE",
    "AWS_ROLE_ARN",
)


@pytest.fixture
def no_credentials(monkeypatch, tmp_path):
    """No credentials anywhere boto3 looks: the environment, the files, the instance."""
    for name in _CREDENTIAL_VARIABLES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("BOTO_CONFIG", str(tmp_path / "boto"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


@pytest.mark.usefixtures("no_credentials")
class TestRequiringCredentials:
    def test_refuses_when_there_are_none(self):
        with pytest.raises(aws.MissingCredentialsError):
            aws.require_credentials()

    def test_passes_when_there_are_some(self, monkeypatch):
        monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
        monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")

        aws.require_credentials()
