import re

from app.ui_templates import build_dashboard_page, build_login_page

FORBIDDEN_WORDING = ("access code", "access-code", "passcode", "private_access_code")


def _assert_common_page(page: str) -> None:
    assert page.lstrip().lower().startswith("<!doctype html>")
    assert '<meta name="viewport"' in page
    assert "width=device-width" in page
    assert "prefers-color-scheme: dark" in page
    # Self-contained: no external scripts, stylesheets or fonts.
    assert not re.search(r"<script[^>]+src=", page, re.IGNORECASE)
    assert not re.search(r"<link[^>]+stylesheet", page, re.IGNORECASE)
    assert "http://" not in page and "https://" not in page
    lowered = page.lower()
    for phrase in FORBIDDEN_WORDING:
        assert phrase not in lowered


def test_login_page_basics() -> None:
    page = build_login_page()
    _assert_common_page(page)
    assert 'action="/login"' in page
    assert 'method="post"' in page
    assert 'name="username"' in page
    assert 'name="password"' in page
    assert "<fieldset disabled" not in page
    assert 'role="alert"' not in page


def test_login_page_escapes_error_and_username_hint() -> None:
    page = build_login_page(error="<script>alert(1)</script>", username_hint='"><img src=x onerror=alert(1)>')
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<img src=x" not in page
    assert 'value="&quot;&gt;&lt;img src=x onerror=alert(1)&gt;"' in page
    assert 'role="alert"' in page


def test_login_page_unavailable_disables_form() -> None:
    page = build_login_page(login_available=False)
    _assert_common_page(page)
    assert "<fieldset disabled" in page
    assert "DASHBOARD_PASSWORD" in page
    assert "Set DASHBOARD_PASSWORD" in page


def test_login_page_unavailable_still_shows_error() -> None:
    page = build_login_page(error="Login unavailable", login_available=False)
    assert "<fieldset disabled" in page
    assert "Login unavailable" in page


def test_dashboard_page_basics_with_auth() -> None:
    page = build_dashboard_page("owner", True)
    _assert_common_page(page)
    assert "owner" in page
    # Logout is a POST form.
    assert re.search(r'<form method="post" action="/logout">', page)
    for endpoint in ("/setup/status", "/documents", "/upload", "/get-document", "/logs/recent", "/files/"):
        assert endpoint in page
    assert "credentials: 'same-origin'" in page
    assert "location.href = '/login'" in page


def test_dashboard_page_without_auth_has_no_logout() -> None:
    page = build_dashboard_page(None, False)
    _assert_common_page(page)
    assert 'action="/logout"' not in page


def test_dashboard_escapes_username() -> None:
    page = build_dashboard_page("<b>evil</b>", True)
    assert "<b>evil</b>" not in page
    assert "&lt;b&gt;evil&lt;/b&gt;" in page


def test_dashboard_script_never_uses_inner_html() -> None:
    page = build_dashboard_page("owner", True)
    scripts = re.findall(r"<script>(.*?)</script>", page, re.DOTALL)
    assert len(scripts) == 1
    assert "innerHTML" not in scripts[0]
    assert "outerHTML" not in scripts[0]
    assert "insertAdjacentHTML" not in scripts[0]
    assert "document.write" not in scripts[0]


def test_dashboard_mentions_yes_confirmation_help() -> None:
    page = build_dashboard_page("owner", True)
    assert "YES" in page
    assert "CANCEL" in page
    assert 'id="ttl-seconds"' in page
