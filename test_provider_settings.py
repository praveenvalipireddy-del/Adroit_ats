"""Offline regression checks for key rotation through existing provider entry points."""
import json
import os
import sys
from unittest.mock import Mock, patch

import config
import provider_settings as settings
import resume_bot
import apollo_api


def main():
    sdk = Mock()
    before_role, after_role = Mock(), Mock()
    before_role.get_secret_value.side_effect = OSError("no instance role yet")
    after_role.get_secret_value.return_value = {"SecretString": '{"GEMINI_API_KEY":"fixture"}'}
    sdk.client.side_effect = [before_role, after_role]
    with patch.dict(sys.modules, {"boto3": sdk, "botocore.config": Mock()}), \
         patch.dict(os.environ, {"AWS_REGION": "ap-southeast-2"}), patch.object(settings, "_client", None):
        try:
            settings._fetch("fixture/keys")
        except OSError:
            pass
        assert settings._client is None
        assert settings._fetch("fixture/keys")["GEMINI_API_KEY"] == "fixture"
        assert settings._client is after_role

    client = Mock()
    payload = {"GEMINI_API_KEY": "fixture-one", "APOLLO_API_KEY": "fixture-apollo",
               "GEMINI_MODEL": "gemini-fixture", "DATABASE_URL": "ignored"}
    client.get_secret_value.return_value = {"SecretString": json.dumps(payload)}
    with patch.dict(os.environ, {"JOBHUNT_PROVIDER_SECRET": "fixture/keys",
                                 "GEMINI_API_KEY": "old-environment-key"}), \
         patch.object(settings, "_client", client), patch.object(settings, "_values", None), \
         patch.object(settings, "_refresh_at", 0), patch.object(settings.time, "monotonic", return_value=1000) as now:
        client.get_secret_value.side_effect = OSError("unreachable at startup")
        assert config.GEMINI_API_KEY == "old-environment-key"
        now.return_value += 31
        client.get_secret_value.side_effect = None
        client.get_secret_value.reset_mock()
        assert config.GEMINI_API_KEY == "fixture-one"
        assert apollo_api.api_key() == "fixture-apollo"
        assert resume_bot._model_chain()[0] == "gemini-fixture"
        assert "DATABASE_URL" not in settings._values
        client.get_secret_value.assert_called_once_with(SecretId="fixture/keys", VersionStage="AWSCURRENT")
        now.return_value += 301
        client.get_secret_value.side_effect = OSError("unreachable")
        assert config.GEMINI_API_KEY == "fixture-one"
        assert client.get_secret_value.call_count == 2
        now.return_value += 31
        client.get_secret_value.side_effect = None
        client.get_secret_value.return_value = {"SecretString": '{"GEMINI_API_KEY":"fixture-two"}'}
        assert config.GEMINI_API_KEY == "fixture-two"
        assert not apollo_api.api_key()
        assert resume_bot._model_chain()[0] == resume_bot.GEMINI_MODEL
        now.return_value += 301
        client.get_secret_value.return_value = {"SecretString": '{"GEMINI_API_KEY":42}'}
        assert config.GEMINI_API_KEY == "fixture-two"
    print("PASS: provider settings refresh, outages, rotation, and removal")


if __name__ == "__main__":
    main()
