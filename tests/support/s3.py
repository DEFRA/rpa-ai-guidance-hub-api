"""Reusable moto S3 test support fixtures."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def moto_s3() -> Iterator[Any]:
    with mock_aws():
        client = boto3.client("s3", region_name="eu-west-2")
        yield client
