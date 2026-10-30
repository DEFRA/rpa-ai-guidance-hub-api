from dataclasses import dataclass
from enum import StrEnum

from app import config


def _role_arn_prefix() -> str:
    return f"arn:aws:iam::{config.get_config().aws_account}:role/"


@dataclass(frozen=True)
class ServiceCaller:
    name: str

    @property
    def arn(self) -> str:
        return f"{_role_arn_prefix()}{self.name}"


class KnownCallers(StrEnum):
    GUIDANCE_HUB_UI = "rpa-ai-guidance-hub-ui"


CALLERS_BY_NAME: dict[str, ServiceCaller] = {
    caller.value: ServiceCaller(name=caller.value) for caller in KnownCallers
}


def get_caller_by_name(name: str) -> ServiceCaller | None:
    return CALLERS_BY_NAME.get(name)


def get_caller_by_arn(arn: str) -> ServiceCaller | None:
    prefix = _role_arn_prefix()

    if not arn.startswith(prefix):
        return None

    return get_caller_by_name(arn.removeprefix(prefix))
