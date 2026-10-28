from unittest.mock import AsyncMock, MagicMock

import fastapi
import pytest

from app.auth import callers, service

UI_ARN = "arn:aws:iam::123456789012:role/rpa-ai-guidance-hub-ui"


@pytest.fixture
def mock_claims(mocker):
    mocker.patch(
        "app.config.get_config",
        return_value=MagicMock(aws_account="123456789012"),
    )

    def _set(sub: str) -> None:
        mocker.patch.object(
            service, "_verify_caller", AsyncMock(return_value={"sub": sub})
        )

    return _set


class TestAllow:
    def test_no_callers_raises(self):
        with pytest.raises(ValueError, match="At least one caller"):
            service.allow()

    def test_unknown_caller_raises(self):
        with pytest.raises(ValueError, match="Unknown caller: unknown-service"):
            service.allow("unknown-service")

    async def test_allowed_caller_is_returned(self, mock_claims):
        mock_claims(UI_ARN)
        check = service.allow(callers.KnownCallers.GUIDANCE_HUB_UI)

        caller = await check(MagicMock())

        assert caller.name == "rpa-ai-guidance-hub-ui"

    async def test_unrecognised_arn_is_forbidden(self, mock_claims):
        mock_claims("arn:aws:iam::123456789012:role/unknown-service")
        check = service.allow(callers.KnownCallers.GUIDANCE_HUB_UI)
        request = MagicMock()

        with pytest.raises(fastapi.HTTPException) as exc:
            await check(request)

        assert exc.value.status_code == fastapi.status.HTTP_403_FORBIDDEN

    async def test_missing_sub_is_forbidden(self, mocker):
        mocker.patch.object(service, "_verify_caller", AsyncMock(return_value={}))
        check = service.allow(callers.KnownCallers.GUIDANCE_HUB_UI)
        request = MagicMock()

        with pytest.raises(fastapi.HTTPException) as exc:
            await check(request)

        assert exc.value.status_code == fastapi.status.HTTP_403_FORBIDDEN


class TestAllowAllKnown:
    async def test_admits_every_known_caller(self, mock_claims):
        check = service.allow_all_known()

        for known in callers.KnownCallers:
            mock_claims(callers.CALLERS_BY_NAME[known].arn)

            caller = await check(MagicMock())

            assert caller.name == known


class TestVerifyCaller:
    @pytest.mark.parametrize(
        "header",
        [None, "", "Bearer", "Bearer ", "Basic abc.def.ghi", "abc.def.ghi"],
    )
    async def test_missing_or_non_bearer_header_is_unauthorised(self, header):
        request = MagicMock(headers={} if header is None else {"Authorization": header})

        with pytest.raises(fastapi.HTTPException) as exc:
            await service._verify_caller(request)

        assert exc.value.status_code == fastapi.status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("scheme", ["Bearer", "bearer"])
    async def test_bearer_prefix_is_stripped(self, mocker, scheme):
        get_key = mocker.patch.object(
            service._jwk_client, "get_signing_key_from_jwt", return_value=MagicMock()
        )
        decode = mocker.patch("jwt.decode", return_value={"sub": "x"})
        request = MagicMock(headers={"Authorization": f"{scheme} abc.def.ghi"})

        assert await service._verify_caller(request) == {"sub": "x"}
        get_key.assert_called_once_with("abc.def.ghi")
        assert decode.call_args.args[0] == "abc.def.ghi"
