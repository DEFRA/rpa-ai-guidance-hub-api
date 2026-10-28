from unittest.mock import MagicMock

from app.auth import callers


class TestServiceCaller:
    def test_arn_derives_from_config(self, mocker):
        mock_config = MagicMock(aws_account="111122223333")
        mocker.patch("app.config.get_config", return_value=mock_config)

        caller = callers.ServiceCaller(name="test-service")

        assert caller.name == "test-service"
        assert caller.arn == "arn:aws:iam::111122223333:role/test-service"


class TestKnownCallers:
    def test_guidance_hub_ui_caller_exists(self):
        caller = callers.get_caller_by_name("rpa-ai-guidance-hub-ui")

        assert caller is not None
        assert caller.name == callers.KnownCallers.GUIDANCE_HUB_UI.value


class TestLookupByName:
    def test_lookup_existing_caller(self):
        caller = callers.get_caller_by_name("rpa-ai-guidance-hub-ui")

        assert caller is not None
        assert caller.name == "rpa-ai-guidance-hub-ui"

    def test_lookup_unknown_caller_returns_none(self):
        caller = callers.get_caller_by_name("unknown-service")

        assert caller is None


class TestLookupByArn:
    def test_lookup_existing_caller(self, mocker):
        mock_config = MagicMock(aws_account="111122223333")
        mocker.patch("app.config.get_config", return_value=mock_config)

        caller = callers.get_caller_by_arn(
            "arn:aws:iam::111122223333:role/rpa-ai-guidance-hub-ui"
        )

        assert caller is not None
        assert caller.name == "rpa-ai-guidance-hub-ui"

    def test_lookup_wrong_account_returns_none(self, mocker):
        mock_config = MagicMock(aws_account="111122223333")
        mocker.patch("app.config.get_config", return_value=mock_config)

        caller = callers.get_caller_by_arn(
            "arn:aws:iam::999999999999:role/rpa-ai-guidance-hub-ui"
        )

        assert caller is None

    def test_lookup_unknown_caller_returns_none(self, mocker):
        mock_config = MagicMock(aws_account="111122223333")
        mocker.patch("app.config.get_config", return_value=mock_config)

        caller = callers.get_caller_by_arn(
            "arn:aws:iam::111122223333:role/unknown-service"
        )

        assert caller is None
