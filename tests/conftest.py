"""Hermetic test setup.

The repository's .env may hold REAL production credentials (Mongo, Twilio, Cloudinary). Environment
variables take precedence over .env in pydantic-settings, so every setting that could reach a real
service is forced here BEFORE any app module is imported.
"""

import os
import socket
import tempfile
from pathlib import Path

import pytest

_TEST_STATE_DIR = Path(tempfile.mkdtemp(prefix="instaretriv-tests-"))

_HERMETIC_ENV = {
    "APP_ENV": "test",
    "APP_SECRET_KEY": "test-secret",
    "METADATA_BACKEND": "json",
    "STORAGE_BACKEND": "local",
    "MONGODB_URI": "",
    "TWILIO_ACCOUNT_SID": "",
    "TWILIO_AUTH_TOKEN": "",
    "TWILIO_SECONDARY_AUTH_TOKEN": "",
    "CLOUDINARY_CLOUD_NAME": "",
    "CLOUDINARY_API_KEY": "",
    "CLOUDINARY_API_SECRET": "",
    "PUBLIC_BASE_URL": "",
    "AUTHORIZED_SENDERS": "",
    "ALLOW_ANY_SENDER": "false",
    "REQUIRE_TWILIO_SIGNATURE": "false",
    "DASHBOARD_AUTH_ENABLED": "false",
    "DASHBOARD_USERNAME": "admin",
    "DASHBOARD_PASSWORD": "",
    "METADATA_FILE": str(_TEST_STATE_DIR / "metadata.json"),
    "REQUEST_LOG_FILE": str(_TEST_STATE_DIR / "request_logs.json"),
    "UPLOAD_DIR": str(_TEST_STATE_DIR / "uploads"),
    "ALLOWED_EXTENSIONS": "pdf,png,jpg,jpeg,webp,doc,docx",
    "TWILIO_WHATSAPP_FROM": "whatsapp:+14155238886",
}
os.environ.update(_HERMETIC_ENV)


# Refuse any outbound connection or DNS lookup that is not loopback, for the whole test process
# (installed before app import, so import-time code is covered too). Loopback stays allowed because
# asyncio on Windows uses a loopback socket pair internally.
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost", "testserver", None}
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _address_host(address):
    return address[0] if isinstance(address, tuple) else None


# Every blocked attempt is recorded and fails the session (see pytest_sessionfinish), because app
# code that catches connection errors (e.g. development fallbacks) could otherwise hide it.
BLOCKED_NETWORK_ATTEMPTS: list[str] = []


def _block(description: str) -> str:
    BLOCKED_NETWORK_ATTEMPTS.append(description)
    return f"external network blocked in tests: {description}"


def _guarded_connect(self, address):
    if _address_host(address) not in _LOOPBACK_HOSTS:
        raise ConnectionRefusedError(_block(f"connect {address!r}"))
    return _real_connect(self, address)


def _guarded_connect_ex(self, address):
    if _address_host(address) not in _LOOPBACK_HOSTS:
        raise ConnectionRefusedError(_block(f"connect {address!r}"))
    return _real_connect_ex(self, address)


def _guarded_getaddrinfo(host, *args, **kwargs):
    lookup = host.decode() if isinstance(host, bytes) else host
    if lookup not in _LOOPBACK_HOSTS:
        raise socket.gaierror(_block(f"DNS {lookup!r}"))
    return _real_getaddrinfo(host, *args, **kwargs)


socket.socket.connect = _guarded_connect
socket.socket.connect_ex = _guarded_connect_ex
socket.getaddrinfo = _guarded_getaddrinfo

from app.config import Settings, settings  # noqa: E402  (must come after the environment is forced)

# Baseline built from the hermetic environment only (never from .env).
_BASELINE_SETTINGS = {name: getattr(Settings(_env_file=None), name) for name in Settings.model_fields}


def _apply_settings(values: dict) -> None:
    for name, value in values.items():
        setattr(settings, name, value)


@pytest.fixture(autouse=True)
def hermetic_app_state(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Fresh settings, stores, sender and in-memory state for every test."""
    _apply_settings(_BASELINE_SETTINGS)

    import app.main as main_module
    from app.request_log_repository import RequestLogRepository
    from app.security import LoginRateLimiter
    from app.services.storage import LocalStorageService
    from app.services.whatsapp import WhatsAppSender
    from app.stores import create_metadata_store

    # A separate directory, so tests that inspect their own tmp_path see only their files.
    state_dir = tmp_path_factory.mktemp("app_state")
    settings.metadata_file = str(state_dir / "metadata.json")
    settings.request_log_file = str(state_dir / "request_logs.json")
    settings.upload_dir = str(state_dir / "uploads")

    monkeypatch.setattr(main_module, "repository", create_metadata_store(settings))
    monkeypatch.setattr(main_module, "request_logs", RequestLogRepository(settings.request_log_file))
    monkeypatch.setattr(
        main_module,
        "storage_service",
        LocalStorageService(settings.upload_dir, max_bytes=settings.max_upload_bytes),
    )
    monkeypatch.setattr(main_module, "whatsapp_sender", WhatsAppSender(account_sid="", auth_token="", sender=""))
    monkeypatch.setattr(main_module, "login_rate_limiter", LoginRateLimiter(max_attempts=5, lockout_seconds=900))
    monkeypatch.setattr(
        main_module, "login_account_rate_limiter", LoginRateLimiter(max_attempts=20, lockout_seconds=900)
    )
    monkeypatch.setattr(main_module, "storage_fallback_reason", None)

    main_module._private_access_challenges.clear()
    main_module._recent_message_sids_queue.clear()
    main_module._recent_message_sids_set.clear()
    main_module._revoked_session_tokens.clear()

    yield main_module

    main_module._private_access_challenges.clear()
    main_module._recent_message_sids_queue.clear()
    main_module._recent_message_sids_set.clear()
    main_module._revoked_session_tokens.clear()
    _apply_settings(_BASELINE_SETTINGS)


@pytest.fixture
def blocked_network_attempts() -> list[str]:
    """The live list of blocked network attempts (for tests of the guard itself)."""
    return BLOCKED_NETWORK_ATTEMPTS


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if BLOCKED_NETWORK_ATTEMPTS:
        print("\nExternal network access was attempted during the tests (blocked):")
        for attempt in BLOCKED_NETWORK_ATTEMPTS:
            print(f"  - {attempt}")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
