"""Read provider credentials from one Secrets Manager secret in the AWS deployment."""
import json
import logging
import os
import threading
import time

API_KEYS = frozenset({
    "GEMINI_API_KEY", "ANTHROPIC_API_KEY", "APIFY_API_TOKEN", "APOLLO_API_KEY",
    "HARVESTAPI_API_KEY", "OPENROUTER_API_KEY", "XAI_API_KEY", "SERPAPI_KEY",
})
_fields = API_KEYS | {"GEMINI_MODEL"}
_lock = threading.Lock()
_client = None
_values = None
_refresh_at = 0.0


def _fetch(secret_id):
    global _client
    client = _client
    if client is None:
        import boto3
        from botocore.config import Config

        client = boto3.client("secretsmanager", region_name=os.environ["AWS_REGION"],
                              config=Config(connect_timeout=2, read_timeout=3,
                                            retries={"total_max_attempts": 2}))
    response = client.get_secret_value(SecretId=secret_id, VersionStage="AWSCURRENT")
    # A client created before EC2 receives its role may have no signing credentials.
    # Reuse the client only after a successful authenticated request.
    _client = client
    data = json.loads(response["SecretString"])
    if not isinstance(data, dict) or not API_KEYS.intersection(data):
        raise ValueError("Expected a JSON object containing provider keys")
    settings = {name: data[name] for name in _fields.intersection(data)}
    if not all(isinstance(value, str) for value in settings.values()):
        raise ValueError("Provider settings must be strings")
    return settings


def get(name, default=None):
    global _values, _refresh_at
    secret_id = os.getenv("JOBHUNT_PROVIDER_SECRET")
    if not secret_id:
        return os.getenv(name, "") if default is None else default
    if time.monotonic() >= _refresh_at:
        with _lock:
            if time.monotonic() >= _refresh_at:
                try:
                    _values = _fetch(secret_id)
                except Exception as exc:
                    # Retain the last working snapshot. Never log the payload or SDK message.
                    logging.getLogger(__name__).warning("Provider secret refresh failed (%s)", type(exc).__name__)
                    _refresh_at = time.monotonic() + 30
                else:
                    _refresh_at = time.monotonic() + 300
    if _values is None:
        # Preserve the existing EC2 configuration if AWS is unavailable on a cold start.
        # Once a secret has loaded, a removed key stays disabled.
        return os.getenv(name, "") if default is None else default
    return _values.get(name, default or "")
