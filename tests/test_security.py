from urllib.parse import parse_qs, urlsplit

import pytest
from twilio.request_validator import RequestValidator

from app import security
from app.security import (
    LoginRateLimiter,
    build_signed_file_url,
    create_session_token,
    get_app_secret,
    is_valid_twilio_request,
    mask_phone,
    sanitize_text_for_logs,
    sign_file_access,
    twilio_candidate_urls,
    validate_session_token,
    verify_file_access,
    verify_password,
)

SECRET = "unit-test-secret"
NOW = 1_700_000_000.0


# --------------------------------------------------------------------------- app secret


def test_get_app_secret_uses_configured_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.settings, "app_secret_key", "configured-secret")
    assert get_app_secret() == "configured-secret"


def test_get_app_secret_generates_stable_random_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.settings, "app_secret_key", "")
    monkeypatch.setattr(security, "_generated_secret", None)
    first = get_app_secret()
    second = get_app_secret()
    assert first == second
    assert len(first) >= 32

    monkeypatch.setattr(security, "_generated_secret", None)
    assert get_app_secret() != first


def test_get_app_secret_warns_once(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(security.settings, "app_secret_key", "   ")
    monkeypatch.setattr(security, "_generated_secret", None)
    with caplog.at_level("WARNING", logger="instaretriv.security"):
        get_app_secret()
        get_app_secret()
    messages = [record for record in caplog.records if "APP_SECRET_KEY" in record.getMessage()]
    assert len(messages) == 1


def test_get_app_secret_logs_error_in_production(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(security.settings, "app_secret_key", "")
    monkeypatch.setattr(security.settings, "app_env", "production")
    monkeypatch.setattr(security, "_generated_secret", None)
    with caplog.at_level("WARNING", logger="instaretriv.security"):
        get_app_secret()
    assert any(record.levelname == "ERROR" for record in caplog.records)


# --------------------------------------------------------------------------- passwords


def test_verify_password_matches_exact_value() -> None:
    assert verify_password("s3cret!", "s3cret!") is True


@pytest.mark.parametrize("provided", ["s3cret", "S3CRET!", "s3cret! ", "", "x" * 100])
def test_verify_password_rejects_mismatch(provided: str) -> None:
    assert verify_password(provided, "s3cret!") is False


@pytest.mark.parametrize("provided", ["", "anything", "admin123"])
def test_verify_password_empty_expected_never_matches(provided: str) -> None:
    assert verify_password(provided, "") is False


def test_verify_password_handles_unicode_and_none() -> None:
    assert verify_password("पासवर्ड✓", "पासवर्ड✓") is True
    assert verify_password(None, "secret") is False  # type: ignore[arg-type]


# --------------------------------------------------------------------------- rate limiter


def test_rate_limiter_locks_after_max_attempts() -> None:
    limiter = LoginRateLimiter(max_attempts=3, lockout_seconds=60)
    for offset in range(2):
        limiter.register_failure("1.2.3.4", now=NOW + offset)
        assert limiter.is_locked("1.2.3.4", now=NOW + offset) is False
    limiter.register_failure("1.2.3.4", now=NOW + 2)
    assert limiter.is_locked("1.2.3.4", now=NOW + 3) is True
    assert limiter.is_locked("5.6.7.8", now=NOW + 3) is False


def test_rate_limiter_lock_expires() -> None:
    limiter = LoginRateLimiter(max_attempts=2, lockout_seconds=60)
    limiter.register_failure("ip", now=NOW)
    limiter.register_failure("ip", now=NOW + 1)
    assert limiter.is_locked("ip", now=NOW + 60) is True
    assert limiter.is_locked("ip", now=NOW + 61) is False
    # After expiry the counter starts fresh.
    limiter.register_failure("ip", now=NOW + 62)
    assert limiter.is_locked("ip", now=NOW + 62) is False


def test_rate_limiter_failures_outside_window_do_not_count() -> None:
    limiter = LoginRateLimiter(max_attempts=3, lockout_seconds=60)
    limiter.register_failure("ip", now=NOW)
    limiter.register_failure("ip", now=NOW + 10)
    limiter.register_failure("ip", now=NOW + 100)  # the first two are older than the window
    assert limiter.is_locked("ip", now=NOW + 100) is False
    limiter.register_failure("ip", now=NOW + 110)
    assert limiter.is_locked("ip", now=NOW + 110) is False
    limiter.register_failure("ip", now=NOW + 120)
    assert limiter.is_locked("ip", now=NOW + 120) is True


def test_rate_limiter_reset_and_clear() -> None:
    limiter = LoginRateLimiter(max_attempts=2, lockout_seconds=60)
    limiter.register_failure("a", now=NOW)
    limiter.register_failure("a", now=NOW)
    assert limiter.is_locked("a", now=NOW) is True
    limiter.reset("a")
    assert limiter.is_locked("a", now=NOW) is False

    # reset also clears partial failure counts
    limiter.register_failure("a", now=NOW)
    limiter.reset("a")
    limiter.register_failure("a", now=NOW)
    assert limiter.is_locked("a", now=NOW) is False

    limiter.register_failure("b", now=NOW)
    limiter.register_failure("b", now=NOW)
    limiter.register_failure("a", now=NOW)
    assert limiter.is_locked("a", now=NOW) and limiter.is_locked("b", now=NOW)
    limiter.clear()
    assert not limiter.is_locked("a", now=NOW) and not limiter.is_locked("b", now=NOW)


def test_rate_limiter_uses_real_clock_by_default() -> None:
    limiter = LoginRateLimiter(max_attempts=1, lockout_seconds=60)
    limiter.register_failure("ip")
    assert limiter.is_locked("ip") is True


def test_rate_limiter_coerces_invalid_limits() -> None:
    limiter = LoginRateLimiter(max_attempts=0, lockout_seconds=0)
    limiter.register_failure("ip", now=NOW)
    assert limiter.is_locked("ip", now=NOW) is True
    assert limiter.is_locked("ip", now=NOW + 2) is False


# --------------------------------------------------------------------------- session tokens


def test_session_token_format_and_roundtrip() -> None:
    token = create_session_token(SECRET, now=NOW)
    issued_at, nonce, signature = token.split(".")
    assert issued_at == str(int(NOW))
    assert nonce
    assert len(signature) == 64
    assert validate_session_token(token, SECRET, ttl_seconds=3600, now=NOW + 10) is True


def test_session_tokens_are_unique() -> None:
    assert create_session_token(SECRET, now=NOW) != create_session_token(SECRET, now=NOW)


def test_session_token_expiry_boundary() -> None:
    token = create_session_token(SECRET, now=NOW)
    assert validate_session_token(token, SECRET, ttl_seconds=60, now=NOW + 60) is True
    assert validate_session_token(token, SECRET, ttl_seconds=60, now=NOW + 61) is False


def test_session_token_rejects_wrong_secret() -> None:
    token = create_session_token(SECRET, now=NOW)
    assert validate_session_token(token, "other-secret", ttl_seconds=3600, now=NOW) is False


def test_session_token_rejects_tampering() -> None:
    token = create_session_token(SECRET, now=NOW)
    issued_at, nonce, signature = token.split(".")
    flipped = ("0" if signature[0] != "0" else "1") + signature[1:]
    candidates = [
        f"{int(issued_at) + 1000}.{nonce}.{signature}",  # extend lifetime
        f"{issued_at}.{nonce}x.{signature}",
        f"{issued_at}.{nonce}.{flipped}",
        f"{issued_at}.{nonce}.{signature.upper()}",
        token + ".extra",
        token[:-1],
    ]
    for candidate in candidates:
        assert validate_session_token(candidate, SECRET, ttl_seconds=3600, now=NOW) is False, candidate


@pytest.mark.parametrize(
    "token",
    ["", "garbage", "a.b.c", "1.2", "-5.nonceabcdef.deadbeef", "x" * 1000, "1700000000..", ". . "],
)
def test_session_token_rejects_malformed(token: str) -> None:
    assert validate_session_token(token, SECRET, ttl_seconds=3600, now=NOW) is False


def test_session_token_clock_skew() -> None:
    within_skew = create_session_token(SECRET, now=NOW + 60)
    beyond_skew = create_session_token(SECRET, now=NOW + 61)
    assert validate_session_token(within_skew, SECRET, ttl_seconds=3600, now=NOW) is True
    assert validate_session_token(beyond_skew, SECRET, ttl_seconds=3600, now=NOW) is False


def test_session_token_rejects_empty_secret_or_ttl() -> None:
    token = create_session_token(SECRET, now=NOW)
    assert validate_session_token(token, "", ttl_seconds=3600, now=NOW) is False
    assert validate_session_token(token, SECRET, ttl_seconds=0, now=NOW) is False


def test_session_token_uses_real_clock_by_default() -> None:
    assert validate_session_token(create_session_token(SECRET), SECRET, ttl_seconds=60) is True


def test_session_signature_cannot_be_reused_as_file_signature() -> None:
    token = create_session_token(SECRET, now=NOW)
    issued_at, _nonce, signature = token.split(".")
    assert verify_file_access("doc-1", issued_at, signature, SECRET, now=NOW) is False


# --------------------------------------------------------------------------- signed file access


def test_file_signature_roundtrip() -> None:
    exp, sig = sign_file_access("doc-1", SECRET, ttl_seconds=900, now=NOW)
    assert exp == int(NOW) + 900
    assert len(sig) == 64
    assert verify_file_access("doc-1", exp, sig, SECRET, now=NOW) is True
    assert verify_file_access("doc-1", str(exp), sig, SECRET, now=NOW + 899) is True


def test_file_signature_expiry_boundary() -> None:
    exp, sig = sign_file_access("doc-1", SECRET, ttl_seconds=900, now=NOW)
    assert verify_file_access("doc-1", exp, sig, SECRET, now=exp) is True
    assert verify_file_access("doc-1", exp, sig, SECRET, now=exp + 1) is False


def test_file_signature_wrong_document_or_secret() -> None:
    exp, sig = sign_file_access("doc-1", SECRET, ttl_seconds=900, now=NOW)
    assert verify_file_access("doc-2", exp, sig, SECRET, now=NOW) is False
    assert verify_file_access("doc-1", exp, sig, "other-secret", now=NOW) is False


def test_file_signature_tampered_expiry_or_sig() -> None:
    exp, sig = sign_file_access("doc-1", SECRET, ttl_seconds=900, now=NOW)
    flipped = ("0" if sig[0] != "0" else "1") + sig[1:]
    assert verify_file_access("doc-1", exp + 3600, sig, SECRET, now=NOW) is False
    assert verify_file_access("doc-1", exp, flipped, SECRET, now=NOW) is False
    assert verify_file_access("doc-1", exp, sig[:-2], SECRET, now=NOW) is False


@pytest.mark.parametrize(
    ("exp", "sig"),
    [
        (None, "a" * 64),
        ("", "a" * 64),
        ("abc", "a" * 64),
        ("-100", "a" * 64),
        ("1e10", "a" * 64),
        ("9" * 40, "a" * 64),
        (True, "a" * 64),
        ("1700000900", None),
        ("1700000900", ""),
        ("1700000900", "not-hex" * 10),
    ],
)
def test_file_signature_rejects_malformed_inputs(exp: object, sig: object) -> None:
    assert verify_file_access("doc-1", exp, sig, SECRET, now=NOW) is False  # type: ignore[arg-type]


def test_file_signature_accepts_uppercase_hex() -> None:
    exp, sig = sign_file_access("doc-1", SECRET, ttl_seconds=900, now=NOW)
    assert verify_file_access("doc-1", exp, sig.upper(), SECRET, now=NOW) is True


def test_file_signature_requires_secret_and_id() -> None:
    exp, sig = sign_file_access("doc-1", SECRET, ttl_seconds=900, now=NOW)
    assert verify_file_access("doc-1", exp, sig, "", now=NOW) is False
    assert verify_file_access("", exp, sig, SECRET, now=NOW) is False


def test_build_signed_file_url_is_verifiable() -> None:
    url = build_signed_file_url("https://bot.example.com/", "doc-123", SECRET, ttl_seconds=900)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://bot.example.com/files/doc-123"
    query = parse_qs(parts.query)
    assert verify_file_access("doc-123", query["exp"][0], query["sig"][0], SECRET) is True
    assert verify_file_access("doc-999", query["exp"][0], query["sig"][0], SECRET) is False


def test_build_signed_file_url_escapes_document_id() -> None:
    url = build_signed_file_url("https://bot.example.com", "../a b?c", SECRET, ttl_seconds=900)
    assert "/files/..%2Fa%20b%3Fc?exp=" in url


# --------------------------------------------------------------------------- twilio candidate URLs


def test_candidate_urls_plain_request_only() -> None:
    urls = twilio_candidate_urls("http://127.0.0.1:8000/webhook", "", None, None)
    assert urls == ["http://127.0.0.1:8000/webhook"]


def test_candidate_urls_order_with_public_base_and_forwarded_headers() -> None:
    urls = twilio_candidate_urls(
        "http://10.0.0.5:10000/webhook?x=1",
        "https://bot.example.com/",
        "https",
        "bot.onrender.com",
    )
    assert urls == [
        "https://bot.example.com/webhook?x=1",
        "https://bot.onrender.com/webhook?x=1",
        "http://10.0.0.5:10000/webhook?x=1",
    ]


def test_candidate_urls_public_base_with_path_prefix() -> None:
    urls = twilio_candidate_urls("http://internal/webhook", "https://example.com/bot", None, None)
    assert urls[0] == "https://example.com/bot/webhook"


def test_candidate_urls_forwarded_proto_only_and_chained_values() -> None:
    urls = twilio_candidate_urls("http://bot.example.com/webhook", "", "https, http", None)
    assert urls == ["https://bot.example.com/webhook", "http://bot.example.com/webhook"]

    urls = twilio_candidate_urls("http://10.0.0.5/webhook", "", None, "public.example.com, proxy.local")
    assert urls == ["http://public.example.com/webhook", "http://10.0.0.5/webhook"]


def test_candidate_urls_are_deduplicated() -> None:
    urls = twilio_candidate_urls(
        "https://bot.example.com/webhook",
        "https://bot.example.com",
        "https",
        "bot.example.com",
    )
    assert urls == ["https://bot.example.com/webhook"]


# --------------------------------------------------------------------------- twilio signature validation

PRIMARY = "primary-fake-token"
SECONDARY = "secondary-fake-token"
FORM = {"Body": "send resume", "From": "whatsapp:+919876543210", "MessageSid": "SM123"}


def _sign(token: str, url: str, form: dict[str, str]) -> str:
    return RequestValidator(token).compute_signature(url, form)


def test_twilio_request_valid_with_primary_token() -> None:
    url = "https://bot.example.com/webhook"
    signature = _sign(PRIMARY, url, FORM)
    assert is_valid_twilio_request(FORM, signature, [PRIMARY, SECONDARY], [url]) is True


def test_twilio_request_valid_with_secondary_token() -> None:
    url = "https://bot.example.com/webhook"
    signature = _sign(SECONDARY, url, FORM)
    assert is_valid_twilio_request(FORM, signature, [PRIMARY, SECONDARY], [url]) is True
    assert is_valid_twilio_request(FORM, signature, [PRIMARY], [url]) is False


def test_twilio_request_matches_any_candidate_url() -> None:
    signed_url = "https://bot.onrender.com/webhook"
    signature = _sign(PRIMARY, signed_url, FORM)
    candidates = twilio_candidate_urls("http://10.0.0.5:10000/webhook", "", "https", "bot.onrender.com")
    assert is_valid_twilio_request(FORM, signature, [PRIMARY], candidates) is True
    assert is_valid_twilio_request(FORM, signature, [PRIMARY], ["http://10.0.0.5:10000/webhook"]) is False


def test_twilio_request_with_query_string() -> None:
    signed_url = "https://bot.example.com/webhook?source=wa"
    signature = _sign(PRIMARY, signed_url, FORM)
    candidates = twilio_candidate_urls("http://internal/webhook?source=wa", "https://bot.example.com", None, None)
    assert is_valid_twilio_request(FORM, signature, [PRIMARY], candidates) is True


def test_twilio_request_rejects_tampered_form() -> None:
    url = "https://bot.example.com/webhook"
    signature = _sign(PRIMARY, url, FORM)
    tampered = dict(FORM, Body="send passport")
    assert is_valid_twilio_request(tampered, signature, [PRIMARY], [url]) is False
    assert is_valid_twilio_request(dict(FORM, Extra="1"), signature, [PRIMARY], [url]) is False


def test_twilio_request_rejects_wrong_token_or_url() -> None:
    url = "https://bot.example.com/webhook"
    signature = _sign(PRIMARY, url, FORM)
    assert is_valid_twilio_request(FORM, signature, ["other-token"], [url]) is False
    assert is_valid_twilio_request(FORM, signature, [PRIMARY], ["https://evil.example.com/webhook"]) is False


def test_twilio_request_rejects_empty_signature_tokens_or_urls() -> None:
    url = "https://bot.example.com/webhook"
    signature = _sign(PRIMARY, url, FORM)
    assert is_valid_twilio_request(FORM, "", [PRIMARY], [url]) is False
    assert is_valid_twilio_request(FORM, "   ", [PRIMARY], [url]) is False
    assert is_valid_twilio_request(FORM, signature, [], [url]) is False
    assert is_valid_twilio_request(FORM, signature, ["", "  "], [url]) is False
    assert is_valid_twilio_request(FORM, signature, [PRIMARY], []) is False


def test_twilio_request_ignores_empty_tokens_alongside_valid_ones() -> None:
    url = "https://bot.example.com/webhook"
    signature = _sign(SECONDARY, url, FORM)
    assert is_valid_twilio_request(FORM, signature, ["", SECONDARY], [url]) is True


def test_twilio_request_signature_of_empty_token_is_not_accepted() -> None:
    # An attacker can compute a signature with an empty key; empty tokens must never be tried.
    url = "https://bot.example.com/webhook"
    forged = _sign("", url, FORM)
    assert is_valid_twilio_request(FORM, forged, ["", PRIMARY], [url]) is False


# --------------------------------------------------------------------------- masking / log sanitizing


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("whatsapp:+919876543210", "+91******3210"),
        ("+14155238886", "+14*****8886"),
        ("whatsapp:+91 98765-43210", "+91******3210"),
        ("1234567", "+12*4567"),
        ("123456", "+****56"),
        ("12345", "+***45"),
        ("1234", "+****"),
        ("12", "+**"),
        ("", ""),
        ("   ", ""),
        ("whatsapp:", "***"),
        ("unknown", "***"),
    ],
)
def test_mask_phone(value: str, expected: str) -> None:
    assert mask_phone(value) == expected


def test_mask_phone_never_contains_full_number() -> None:
    assert "9876543210" not in mask_phone("whatsapp:+919876543210")


@pytest.mark.parametrize(
    ("text", "forbidden"),
    [
        ("my code is 4821", "4821"),
        ("PIN: 9911", "9911"),
        ("otp=abc123", "abc123"),
        ("passcode hunter2", "hunter2"),
        ("password=letmein", "letmein"),
        ("send pan 12345678", "12345678"),
        ("account 1234", "1234"),
    ],
)
def test_sanitize_redacts_secrets_and_digit_runs(text: str, forbidden: str) -> None:
    sanitized = sanitize_text_for_logs(text)
    assert forbidden not in sanitized


def test_sanitize_keeps_normal_text_and_short_numbers() -> None:
    assert sanitize_text_for_logs("Send my Resume 2") == "Send my Resume 2"
    assert sanitize_text_for_logs("class 10 marksheet") == "class 10 marksheet"
    assert sanitize_text_for_logs("barcode scan") == "barcode scan"


def test_sanitize_redaction_markers() -> None:
    assert sanitize_text_for_logs("my code is 4821") == "my code [redacted] ****"
    assert sanitize_text_for_logs("pin 4821") == "pin [redacted]"
    assert sanitize_text_for_logs("send 4821") == "send ****"


def test_sanitize_masks_phone_numbers() -> None:
    sanitized = sanitize_text_for_logs("call +919876543210 now")
    assert "9876543210" not in sanitized
    assert "+91******3210" in sanitized


def test_sanitize_collapses_whitespace_and_control_chars() -> None:
    assert sanitize_text_for_logs("  send\n\nmy\tresume \r\n fake-log-line\x00\x1b ") == (
        "send my resume fake-log-line"
    )


def test_sanitize_truncates_with_ellipsis() -> None:
    sanitized = sanitize_text_for_logs("word " * 100, max_len=20)
    assert len(sanitized) <= 20
    assert sanitized.endswith("…")
    assert sanitize_text_for_logs("short", max_len=20) == "short"
    assert sanitize_text_for_logs("x" * 120) == "x" * 120
    assert len(sanitize_text_for_logs("x" * 121)) == 120


def test_sanitize_handles_empty_and_non_positive_limits() -> None:
    assert sanitize_text_for_logs("") == ""
    assert sanitize_text_for_logs(None) == ""  # type: ignore[arg-type]
    assert sanitize_text_for_logs("hello", max_len=0) == ""
