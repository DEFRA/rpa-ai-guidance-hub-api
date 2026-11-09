"""What the API needs from AWS before it can start."""

import boto3


class MissingCredentialsError(RuntimeError):
    """Raised at startup when there are no AWS credentials to be had."""


def require_credentials() -> None:
    """Refuse to start without AWS credentials, which the buckets and the queue need.

    Asked for by itself, because making a client asks nothing: boto3 makes one with
    no credentials at all, and fails only when it is first used.
    """
    if boto3.Session().get_credentials() is None:
        message = "No AWS credentials: the buckets and the queue cannot be used"
        raise MissingCredentialsError(message)
