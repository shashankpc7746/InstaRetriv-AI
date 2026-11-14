"""Server-rendered HTML for the InstaRetriv dashboard and login pages.

Both pages are fully self-contained (no external fonts, scripts or images) so they
work behind strict networks and never leak requests to third parties.

Security rules for this module:
- Every server-side value interpolated into HTML goes through ``html.escape``.
- Data loaded by the dashboard script is rendered with DOM APIs / ``textContent``
  only, never ``innerHTML``.
"""

import html

_LOGO_SVG = (
    '<svg class="logo" viewBox="0 0 32 32" width="32" height="32" aria-hidden="true" focusable="false">'
    '<rect x="1" y="1" width="30" height="30" rx="9" fill="var(--brand)"/>'
    '<path d="M10 9.5h8.5l4 4V22a1.5 1.5 0 0 1-1.5 1.5H10A1.5 1.5 0 0 1 8.5 22V11A1.5 1.5 0 0 1 10 9.5z" '
    'fill="none" stroke="var(--brand-contrast)" stroke-width="1.8" stroke-linejoin="round"/>'
    '<path d="M12 16.5l2.6 2.5 5-5" fill="none" stroke="var(--brand-contrast)" stroke-width="1.8" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)

_BASE_CSS = """
:root {
  color-scheme: light dark;
  --bg: #f5f6f8;
  --surface: #ffffff;
  --surface-2: #f0f2f5;
  --text: #111827;
  --muted: #586274;
  --border: #dfe3e8;
  --brand: #0f766e;
  --brand-hover: #0b5f59;
  --brand-contrast: #ffffff;
  --brand-soft: #e3f3f1;
  --danger: #b42318;
  --danger-soft: #fef1f0;
  --warn: #93370d;
  --warn-soft: #fff7e6;
  --warn-border: #f5c46b;
  --ok: #067647;
  --ok-soft: #eafbf1;
  --focus: #2563eb;
  --shadow: 0 1px 2px rgba(16, 24, 40, 0.05), 0 6px 18px rgba(16, 24, 40, 0.06);
  --radius: 14px;
  --font: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0e1116;
    --surface: #161a21;
    --surface-2: #1d222b;
    --text: #e8eaef;
    --muted: #a0a8b6;
    --border: #2b313c;
    --brand: #2bc4b0;
    --brand-hover: #4fd6c4;
    --brand-contrast: #04201d;
    --brand-soft: rgba(43, 196, 176, 0.13);
    --danger: #ff8a80;
    --danger-soft: rgba(240, 68, 56, 0.14);
    --warn: #fdc35a;
    --warn-soft: rgba(247, 144, 9, 0.13);
    --warn-border: rgba(253, 195, 90, 0.45);
    --ok: #5fd99a;
    --ok-soft: rgba(23, 178, 106, 0.14);
    --focus: #7cb0ff;
    --shadow: 0 1px 2px rgba(0, 0, 0, 0.4);
  }
}
*, *::before, *::after { box-sizing: border-box; }
html { -webkit-text-size-adjust: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font-family: var(--font);
  font-size: 16px;
  line-height: 1.5;
}
a { color: var(--brand); }
a:hover { color: var(--brand-hover); }
h1, h2, h3 { line-height: 1.25; margin: 0; }
p { margin: 0; }
code, .mono { font-family: var(--mono); font-size: 0.92em; }
code { background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px; padding: 1px 6px; }
:focus { outline: none; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 2px; border-radius: 6px; }
.sr-only {
  position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
}
.logo { flex: none; display: block; }
label { font-weight: 600; font-size: 0.95rem; }
input[type="text"], input[type="password"], input[type="search"], input[type="file"] {
  width: 100%;
  font: inherit;
  color: var(--text);
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 11px 12px;
  min-height: 44px;
}
input[type="file"] { padding: 9px 10px; background: var(--surface-2); cursor: pointer; }
input:focus-visible { outline: 3px solid var(--focus); outline-offset: 1px; border-color: var(--focus); }
input:disabled { opacity: 0.6; cursor: not-allowed; }
.btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 8px;
  min-height: 44px; padding: 10px 18px;
  font: inherit; font-weight: 650; text-decoration: none;
  color: var(--brand-contrast); background: var(--brand);
  border: 1px solid transparent; border-radius: 10px; cursor: pointer;
}
.btn:hover { background: var(--brand-hover); color: var(--brand-contrast); }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }
.btn-secondary { color: var(--text); background: var(--surface); border-color: var(--border); }
.btn-secondary:hover { background: var(--surface-2); color: var(--text); }
.btn-danger { color: var(--danger); background: transparent; border-color: var(--border); }
.btn-danger:hover { background: var(--danger-soft); color: var(--danger); }
.btn-small { min-height: 36px; padding: 6px 12px; font-size: 0.9rem; }
.alert {
  border: 1px solid var(--border);
  border-left-width: 5px;
  border-radius: 10px;
  padding: 12px 14px;
  background: var(--surface);
}
.alert-title { font-weight: 700; margin-bottom: 2px; }
.alert-error { border-color: var(--danger); background: var(--danger-soft); }
.alert-error .alert-title { color: var(--danger); }
.alert-warn { border-color: var(--warn-border); border-left-color: var(--warn); background: var(--warn-soft); }
.alert-warn .alert-title { color: var(--warn); }
.alert-ok { border-color: var(--ok); background: var(--ok-soft); }
.alert-ok .alert-title { color: var(--ok); }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition: none !important; animation: none !important; }
}
"""

_LOGIN_CSS = """
.login-wrap { min-height: 100vh; display: grid; place-items: center; padding: 24px 16px; }
.login-card {
  width: 100%; max-width: 400px;
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);
  padding: 28px 24px;
}
.brand-row { display: flex; align-items: center; gap: 10px; margin-bottom: 18px; }
.brand-name { font-weight: 750; font-size: 1.1rem; }
.login-card h1 { font-size: 1.4rem; margin-bottom: 4px; }
.login-card .lead { color: var(--muted); margin-bottom: 18px; }
.login-card .alert { margin-bottom: 16px; }
fieldset { border: 0; margin: 0; padding: 0; min-width: 0; display: grid; gap: 14px; }
.field { display: grid; gap: 6px; }
.login-card .btn { width: 100%; margin-top: 4px; }
.foot { margin-top: 18px; font-size: 0.85rem; color: var(--muted); text-align: center; }
"""


def _page(title: str, body: str, extra_css: str = "", script: str = "") -> str:
    """Wrap body markup in a complete, self-contained HTML document."""
    script_block = f"<script>{script}</script>" if script else ""
    return (
        "<!DOCTYPE html>\n"
        '<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex, nofollow">\n'
        '<meta name="referrer" content="same-origin">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"<title>{html.escape(title)}</title>\n"
        f"<style>{_BASE_CSS}{extra_css}</style>\n"
        "</head>\n<body>\n"
        f"{body}\n"
        f"{script_block}\n"
        "</body>\n</html>\n"
    )


def build_login_page(error: str | None = None, login_available: bool = True, username_hint: str = "") -> str:
    """Render the dashboard sign-in page.

    ``error`` is shown in an inline banner; ``login_available=False`` disables the
    form and explains that DASHBOARD_PASSWORD must be configured first.
    """
    parts: list[str] = []

    if not login_available:
        parts.append(
            '<div class="alert alert-warn" role="alert">'
            '<p class="alert-title">Sign-in is turned off</p>'
            "<p>No dashboard password has been configured yet. "
            "Set DASHBOARD_PASSWORD in the server environment (for example on Render: "
            "<strong>Environment</strong> settings), then restart the app.</p>"
            "</div>"
        )

    if error:
        parts.append(
            '<div class="alert alert-error" role="alert" id="login-error">'
            '<p class="alert-title">Could not sign you in</p>'
            f"<p>{html.escape(error)}</p>"
            "</div>"
        )

    disabled_attr = "" if login_available else " disabled"
    described_by = ' aria-describedby="login-error"' if error else ""
    autofocus_user = "" if username_hint else " autofocus"
    autofocus_pass = " autofocus" if username_hint else ""
    if not login_available:
        autofocus_user = autofocus_pass = ""

    body = f"""
<main class="login-wrap">
  <div class="login-card">
    <div class="brand-row">{_LOGO_SVG}<span class="brand-name">InstaRetriv AI</span></div>
    <h1>Sign in</h1>
    <p class="lead">Manage the documents your WhatsApp assistant can send.</p>
    {''.join(parts)}
    <form method="post" action="/login" autocomplete="on"{described_by}>
      <fieldset{disabled_attr}>
        <legend class="sr-only">Dashboard sign-in</legend>
        <div class="field">
          <label for="username">Username</label>
          <input type="text" id="username" name="username" autocomplete="username"
                 autocapitalize="none" spellcheck="false" required
                 value="{html.escape(username_hint, quote=True)}"{autofocus_user}>
        </div>
        <div class="field">
          <label for="password">Password</label>
          <input type="password" id="password" name="password" autocomplete="current-password"
                 required{autofocus_pass}>
        </div>
        <button class="btn" type="submit">Sign in</button>
      </fieldset>
    </form>
    <p class="foot">Too many wrong attempts will temporarily lock sign-in.</p>
  </div>
</main>
"""
    return _page("Sign in · InstaRetriv AI", body, extra_css=_LOGIN_CSS)


_DASHBOARD_CSS = """
.topbar {
  position: sticky; top: 0; z-index: 10;
  background: var(--surface); border-bottom: 1px solid var(--border);
}
.topbar-inner {
  max-width: 1180px; margin: 0 auto; padding: 10px 16px;
  display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
}
.brand-name { font-weight: 750; font-size: 1.1rem; margin-right: auto; }
.who { color: var(--muted); font-size: 0.92rem; }
.who strong { color: var(--text); }
.topbar form { margin: 0; }
.shell { max-width: 1180px; margin: 0 auto; padding: 20px 16px 48px; display: grid; gap: 20px; }
.card {
  background: var(--surface); border: 1px solid var(--border);
  border-radius: var(--radius); box-shadow: var(--shadow);
  padding: 20px; min-width: 0;
}
.card-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; margin-bottom: 14px; }
.card-head h2 { font-size: 1.12rem; margin-right: auto; }
.card-sub { color: var(--muted); font-size: 0.93rem; margin: -8px 0 14px; }
.grid-2 { display: grid; gap: 20px; grid-template-columns: minmax(0, 1.15fr) minmax(0, 0.85fr); align-items: start; }
.stack { display: grid; gap: 20px; min-width: 0; }
#alerts { display: grid; gap: 10px; }
#alerts ul { margin: 6px 0 0; padding-left: 20px; }
#alerts li + li { margin-top: 4px; }
.status-grid { display: grid; gap: 10px; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr)); }
.status-item {
  border: 1px solid var(--border); border-radius: 12px; padding: 12px 14px;
  background: var(--surface); display: grid; gap: 4px; min-width: 0; align-content: start;
}
.status-label { font-size: 0.82rem; color: var(--muted); font-weight: 600; text-transform: uppercase; letter-spacing: 0.03em; }
.status-value { display: flex; align-items: center; gap: 8px; font-weight: 650; overflow-wrap: anywhere; }
.dot { width: 10px; height: 10px; border-radius: 50%; flex: none; background: var(--muted); }
.state-ok .dot { background: var(--ok); }
.state-warn .dot { background: var(--warn); }
.state-bad .dot { background: var(--danger); }
.state-bad { border-color: var(--danger); }
.status-note { font-size: 0.85rem; color: var(--muted); overflow-wrap: anywhere; }
form.stacked { display: grid; gap: 14px; }
.field { display: grid; gap: 6px; }
.hint { color: var(--muted); font-size: 0.87rem; font-weight: 400; }
.check {
  display: flex; gap: 12px; align-items: flex-start;
  padding: 12px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface-2);
}
.check input { width: 20px; height: 20px; margin: 2px 0 0; flex: none; accent-color: var(--brand); }
.check label { display: grid; gap: 2px; cursor: pointer; }
.row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; }
.row > input { flex: 1 1 200px; width: auto; }
.msg { min-height: 0; }
.msg:empty { display: none; }
.badge {
  display: inline-block; padding: 2px 9px; border-radius: 999px;
  font-size: 0.78rem; font-weight: 700; white-space: nowrap;
  background: var(--surface-2); color: var(--muted); border: 1px solid var(--border);
}
.badge-private { background: var(--warn-soft); color: var(--warn); border-color: var(--warn-border); }
.badge-ok { background: var(--ok-soft); color: var(--ok); border-color: transparent; }
.badge-no { background: var(--danger-soft); color: var(--danger); border-color: transparent; }
.badge-count { background: var(--brand-soft); color: var(--brand); border-color: transparent; }
.tags { display: flex; flex-wrap: wrap; gap: 4px; }
.tag { font-size: 0.8rem; padding: 1px 8px; border-radius: 6px; background: var(--surface-2); border: 1px solid var(--border); }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.94rem; }
th, td { text-align: left; padding: 10px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
th { font-size: 0.8rem; text-transform: uppercase; letter-spacing: 0.03em; color: var(--muted); font-weight: 700; }
tbody tr:last-child td { border-bottom: 0; }
td.name { font-weight: 600; overflow-wrap: anywhere; min-width: 160px; }
td.actions { white-space: nowrap; text-align: right; }
td.actions .btn + .btn { margin-left: 6px; }
.muted { color: var(--muted); }
.empty { color: var(--muted); padding: 18px 4px; text-align: center; }
.result { display: grid; gap: 8px; }
.result-name { font-weight: 700; overflow-wrap: anywhere; }
.help ol { margin: 0; padding-left: 20px; display: grid; gap: 8px; }
.help p + p, .help ol + p { margin-top: 10px; }
.kbd { font-family: var(--mono); font-weight: 700; background: var(--surface-2); border: 1px solid var(--border); border-radius: 6px; padding: 0 6px; }
.small { font-size: 0.87rem; }
@media (max-width: 900px) {
  .grid-2 { grid-template-columns: minmax(0, 1fr); }
}
@media (max-width: 720px) {
  .card { padding: 16px; }
  .who { order: 3; width: 100%; }
  table.responsive thead { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0, 0, 0, 0); }
  table.responsive, table.responsive tbody, table.responsive tr, table.responsive td { display: block; width: 100%; }
  table.responsive tr { border: 1px solid var(--border); border-radius: 12px; padding: 6px 12px; margin-bottom: 10px; }
  table.responsive td { border: 0; padding: 6px 0; display: grid; grid-template-columns: 96px minmax(0, 1fr); gap: 10px; text-align: left; }
  table.responsive td::before { content: attr(data-label); color: var(--muted); font-size: 0.8rem; font-weight: 700; text-transform: uppercase; padding-top: 2px; }
  table.responsive td.actions { display: flex; gap: 8px; padding-top: 10px; }
  table.responsive td.actions::before { content: none; }
  table.responsive td.actions .btn { flex: 1; }
  td.actions .btn + .btn { margin-left: 0; }
}
"""

_DASHBOARD_SCRIPT = r"""
(function () {
  'use strict';

  var state = { status: null, documents: [], maxUploadMb: 15, allowedExtensions: [] };

  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        var value = attrs[key];
        if (value === null || value === undefined || value === false) { return; }
        if (key === 'text') { node.textContent = String(value); }
        else if (key === 'className') { node.className = value; }
        else { node.setAttribute(key, value === true ? '' : String(value)); }
      });
    }
    (children || []).forEach(function (child) {
      if (child === null || child === undefined) { return; }
      node.appendChild(typeof child === 'string' ? document.createTextNode(child) : child);
    });
    return node;
  }

  function clear(node) { while (node.firstChild) { node.removeChild(node.firstChild); } }

  function errorDetail(data, fallback) {
    if (!data) { return fallback; }
    var detail = data.detail !== undefined ? data.detail : data.message;
    if (typeof detail === 'string' && detail) { return detail; }
    if (Array.isArray(detail) && detail.length) {
      return detail.map(function (item) {
        return item && item.msg ? String(item.msg) : String(item);
      }).join('; ');
    }
    return fallback;
  }

  function api(path, options) {
    var opts = options || {};
    var controller = typeof AbortController !== 'undefined' ? new AbortController() : null;
    var timeoutMs = opts.timeoutMs || 30000;
    var timer = controller ? setTimeout(function () { controller.abort(); }, timeoutMs) : null;
    var init = {
      method: opts.method || 'GET',
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' },
      body: opts.body
    };
    if (controller) { init.signal = controller.signal; }
    return fetch(path, init).then(function (response) {
      if (timer) { clearTimeout(timer); }
      if (response.status === 401) {
        location.href = '/login';
        throw new Error('Your session has ended. Redirecting to sign in…');
      }
      return response.text().then(function (text) {
        var data = null;
        if (text) {
          try { data = JSON.parse(text); } catch (parseError) { data = null; }
        }
        if (!response.ok) {
          var fallback = 'Request failed (HTTP ' + response.status + ').';
          if (response.status === 503) { fallback = 'The service is temporarily unavailable. Please try again shortly.'; }
          if (response.status === 413) { fallback = 'That file is too large.'; }
          throw new Error(errorDetail(data, fallback));
        }
        return data;
      });
    }, function (error) {
      if (timer) { clearTimeout(timer); }
      if (error && error.name === 'AbortError') {
        throw new Error('The server took too long to respond. Please try again.');
      }
      throw new Error('Could not reach the server. Check your connection and try again.');
    });
  }

  function showMessage(target, kind, title, text) {
    clear(target);
    if (!title && !text) { return; }
    var box = el('div', {
      className: 'alert alert-' + kind,
      role: kind === 'error' ? 'alert' : 'status'
    }, [
      title ? el('p', { className: 'alert-title', text: title }) : null,
      text ? el('p', { text: text }) : null
    ]);
    target.appendChild(box);
  }

  function formatDate(value) {
    if (!value) { return ''; }
    var date = new Date(value);
    if (isNaN(date.getTime())) { return String(value); }
    try {
      return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
    } catch (e) {
      return date.toLocaleString();
    }
  }

  function fileUrl(id) { return '/files/' + encodeURIComponent(id); }

  /* ---------- Setup status ---------- */

  function statusItem(label, value, level, note) {
    return el('div', { className: 'status-item state-' + level }, [
      el('span', { className: 'status-label', text: label }),
      el('span', { className: 'status-value' }, [
        el('span', { className: 'dot', 'aria-hidden': 'true' }),
        el('span', { text: value }),
        el('span', { className: 'sr-only', text: level === 'ok' ? '(good)' : level === 'warn' ? '(needs attention)' : '(problem)' })
      ]),
      note ? el('span', { className: 'status-note', text: note }) : null
    ]);
  }

  function renderStatus(status) {
    var grid = $('status-grid');
    clear(grid);
    var store = status.metadata_store || {};
    var storeLevel = store.available === false ? 'bad' : (store.fallback ? 'warn' : 'ok');
    var storeValue = store.available === false ? 'Unavailable' : (store.fallback ? 'Temporary fallback' : 'Connected');
    var storeNote = (store.backend ? 'Backend: ' + store.backend : '') + (store.error ? ' · ' + store.error : '');
    grid.appendChild(statusItem('Document database', storeValue, storeLevel, storeNote));

    var senderOn = !!status.twilio_sender_enabled;
    grid.appendChild(statusItem('WhatsApp sending', senderOn ? 'On' : 'Off',
      senderOn ? 'ok' : 'warn', senderOn ? 'Files are delivered on WhatsApp.' : 'Twilio credentials are missing.'));

    var sigOn = !!status.twilio_signature_required;
    grid.appendChild(statusItem('Webhook protection', sigOn ? 'Signature checked' : 'Not checked',
      sigOn ? 'ok' : 'warn', sigOn ? 'Only genuine Twilio requests are accepted.' : 'Anyone could call the webhook.'));

    var storage = status.storage_backend_active || 'unknown';
    var storageLabels = { cloudinary: 'Cloudinary', local: 'Server disk', unavailable: 'Unavailable' };
    var storageNote = storage === 'unavailable' ? 'Uploads are paused.'
      : storage === 'local' ? 'Files are kept on this server.' : null;
    grid.appendChild(statusItem('File storage', storageLabels[storage] || String(storage),
      storage === 'unavailable' ? 'bad' : 'ok', storageNote));

    if (typeof status.authorized_senders_count === 'number') {
      var count = status.authorized_senders_count;
      var enforced = !!status.sender_allowlist_enforced;
      grid.appendChild(statusItem('Allowed WhatsApp numbers',
        enforced ? String(count) + (count === 1 ? ' number' : ' numbers') : 'Any number',
        enforced ? 'ok' : 'warn', enforced ? null : 'Set AUTHORIZED_SENDERS to restrict access.'));
    }

    var authOn = status.dashboard_auth_enabled !== false;
    grid.appendChild(statusItem('Dashboard sign-in', authOn ? 'Required' : 'Off',
      authOn ? 'ok' : 'warn', authOn ? null : 'Anyone who can open this page can manage files.'));
  }

  function renderWarnings(warnings) {
    var box = $('alerts');
    clear(box);
    if (!warnings.length) {
      box.appendChild(el('div', { className: 'alert alert-ok', role: 'status' }, [
        el('p', { className: 'alert-title', text: 'Everything looks set up' }),
        el('p', { text: 'No configuration problems were detected.' })
      ]));
      return;
    }
    var list = el('ul');
    warnings.forEach(function (warning) { list.appendChild(el('li', { text: String(warning) })); });
    box.appendChild(el('div', { className: 'alert alert-warn', role: 'alert' }, [
      el('p', {
        className: 'alert-title',
        text: warnings.length === 1 ? '1 thing needs your attention' : warnings.length + ' things need your attention'
      }),
      list
    ]));
  }

  function applyUploadSettings(status) {
    if (Array.isArray(status.allowed_extensions)) {
      state.allowedExtensions = status.allowed_extensions.map(function (ext) {
        return String(ext).toLowerCase().replace(/^\./, '');
      });
      var accept = state.allowedExtensions.map(function (ext) { return '.' + ext; }).join(',');
      if (accept) { $('file').setAttribute('accept', accept); }
    }
    if (typeof status.max_upload_mb === 'number' && status.max_upload_mb > 0) {
      state.maxUploadMb = status.max_upload_mb;
    }
    var types = state.allowedExtensions.length
      ? state.allowedExtensions.map(function (ext) { return ext.toUpperCase(); }).join(', ')
      : 'any type';
    $('file-hint').textContent = 'Allowed: ' + types + ' · up to ' + state.maxUploadMb + ' MB.';
    if (status.storage_backend_active === 'unavailable') {
      showMessage($('upload-msg'), 'warn', 'Uploads are paused',
        'File storage is not available right now, so uploads will fail until it is fixed.');
    }
  }

  function applyHelp(status) {
    var ttl = Number(status.private_confirmation_ttl_seconds);
    if (ttl > 0) { $('ttl-seconds').textContent = String(ttl); }
  }

  function loadStatus() {
    return api('/setup/status').then(function (status) {
      status = status || {};
      state.status = status;
      renderWarnings(Array.isArray(status.warnings) ? status.warnings : []);
      renderStatus(status);
      applyUploadSettings(status);
      applyHelp(status);
    }).catch(function (error) {
      showMessage($('alerts'), 'error', 'Could not load setup status', error.message);
      clear($('status-grid'));
    });
  }

  /* ---------- Documents ---------- */

  function tagList(tags) {
    var wrap = el('div', { className: 'tags' });
    (tags || []).forEach(function (tag) { wrap.appendChild(el('span', { className: 'tag', text: String(tag) })); });
    if (!wrap.firstChild) { wrap.appendChild(el('span', { className: 'muted', text: '—' })); }
    return wrap;
  }

  function archiveDocument(doc, button) {
    var ok = window.confirm('Archive "' + doc.file_name + '"?\n\nIt will no longer be sent on WhatsApp or opened from here.');
    if (!ok) { return; }
    button.disabled = true;
    api('/documents/' + encodeURIComponent(doc.id), { method: 'DELETE' }).then(function () {
      showMessage($('docs-msg'), 'ok', 'Archived', '"' + doc.file_name + '" was archived.');
      loadDocuments();
    }).catch(function (error) {
      button.disabled = false;
      showMessage($('docs-msg'), 'error', 'Could not archive the document', error.message);
    });
  }

  function documentRow(doc) {
    // Single wrapper so name + badge stay in one grid cell in the mobile card layout.
    var nameWrap = el('span', null, [String(doc.file_name || 'Untitled')]);
    if (doc.is_private) {
      nameWrap.appendChild(document.createTextNode(' '));
      nameWrap.appendChild(el('span', { className: 'badge badge-private', text: 'Private' }));
    }
    var nameCell = el('td', { className: 'name', 'data-label': 'File' }, [nameWrap]);
    var open = el('a', {
      className: 'btn btn-secondary btn-small',
      href: fileUrl(doc.id),
      target: '_blank',
      rel: 'noopener',
      text: 'Open'
    });
    open.setAttribute('aria-label', 'Open ' + (doc.file_name || 'document'));
    var archive = el('button', { className: 'btn btn-danger btn-small', type: 'button', text: 'Archive' });
    archive.setAttribute('aria-label', 'Archive ' + (doc.file_name || 'document'));
    archive.addEventListener('click', function () { archiveDocument(doc, archive); });
    return el('tr', null, [
      nameCell,
      el('td', { 'data-label': 'Category', text: doc.doc_category || '—' }),
      el('td', { 'data-label': 'Tags' }, [tagList(doc.tags)]),
      el('td', { 'data-label': 'Uploaded', className: 'muted', text: formatDate(doc.uploaded_at) }),
      el('td', { className: 'actions' }, [open, archive])
    ]);
  }

  function renderDocuments() {
    var body = $('docs-body');
    clear(body);
    var filter = ($('docs-filter').value || '').trim().toLowerCase();
    var docs = state.documents.filter(function (doc) {
      if (!filter) { return true; }
      var haystack = [doc.file_name, doc.doc_category].concat(doc.tags || []).join(' ').toLowerCase();
      return haystack.indexOf(filter) !== -1;
    });
    $('docs-count').textContent = String(state.documents.length);
    if (!docs.length) {
      var text = state.documents.length
        ? 'No documents match "' + filter + '".'
        : 'No documents yet. Upload your first file above.';
      body.appendChild(el('tr', null, [el('td', { colspan: '5', className: 'empty', text: text })]));
      return;
    }
    docs.forEach(function (doc) { body.appendChild(documentRow(doc)); });
  }

  function loadDocuments() {
    var body = $('docs-body');
    return api('/documents').then(function (docs) {
      state.documents = Array.isArray(docs) ? docs : [];
      renderDocuments();
    }).catch(function (error) {
      clear(body);
      body.appendChild(el('tr', null, [el('td', { colspan: '5', className: 'empty', text: 'Documents could not be loaded.' })]));
      showMessage($('docs-msg'), 'error', 'Could not load documents', error.message);
    });
  }

  /* ---------- Upload ---------- */

  function onUpload(event) {
    event.preventDefault();
    var form = $('upload-form');
    var msg = $('upload-msg');
    var input = $('file');
    var file = input.files && input.files[0];
    if (!file) {
      showMessage(msg, 'error', 'Choose a file first', 'Pick the document you want to upload.');
      input.focus();
      return;
    }
    var ext = (file.name.split('.').pop() || '').toLowerCase();
    if (state.allowedExtensions.length && (file.name.indexOf('.') === -1 || state.allowedExtensions.indexOf(ext) === -1)) {
      showMessage(msg, 'error', 'This file type is not allowed',
        'Allowed types: ' + state.allowedExtensions.join(', ') + '.');
      return;
    }
    if (file.size === 0) {
      showMessage(msg, 'error', 'This file is empty', 'Please choose a different file.');
      return;
    }
    if (file.size > state.maxUploadMb * 1024 * 1024) {
      showMessage(msg, 'error', 'This file is too large',
        'The limit is ' + state.maxUploadMb + ' MB (WhatsApp cannot send bigger files).');
      return;
    }
    var data = new FormData();
    data.append('file', file);
    data.append('doc_category', $('category').value.trim());
    data.append('tags', $('tags').value.trim());
    data.append('is_private', $('is_private').checked ? 'true' : 'false');

    var button = $('upload-btn');
    button.disabled = true;
    button.textContent = 'Uploading…';
    showMessage(msg, 'ok', null, null);
    api('/upload', { method: 'POST', body: data, timeoutMs: 180000 }).then(function (result) {
      var doc = (result && result.document) || {};
      var details = 'Saved as category "' + (doc.doc_category || 'general') + '"';
      if (doc.tags && doc.tags.length) { details += ' with tags: ' + doc.tags.join(', '); }
      details += '.';
      if (doc.is_private) { details += ' WhatsApp will ask for a YES before sending it.'; }
      showMessage(msg, 'ok', 'Uploaded "' + (doc.file_name || file.name) + '"', details);
      form.reset();
      loadDocuments();
    }).catch(function (error) {
      showMessage(msg, 'error', 'Upload failed', error.message);
    }).then(function () {
      button.disabled = false;
      button.textContent = 'Upload document';
    });
  }

  /* ---------- Test query ---------- */

  function onTestQuery(event) {
    event.preventDefault();
    var query = $('test-query').value.trim();
    var out = $('test-result');
    if (!query) {
      showMessage(out, 'error', 'Type a message to test', 'For example: "send my resume".');
      return;
    }
    var button = $('test-btn');
    button.disabled = true;
    clear(out);
    out.appendChild(el('p', { className: 'muted', role: 'status', text: 'Searching…' }));
    api('/get-document?query=' + encodeURIComponent(query)).then(function (result) {
      clear(out);
      if (!result || !result.found || !result.document) {
        showMessage(out, 'warn', 'No match',
          'WhatsApp would reply "Document not found". Try a different word, or add tags to the document.');
        return;
      }
      var doc = result.document;
      var score = typeof result.score === 'number' ? Math.round(result.score * 100) / 100 : null;
      var box = el('div', { className: 'alert alert-ok result', role: 'status' }, [
        el('p', { className: 'alert-title', text: 'Match found' }),
        el('p', { className: 'result-name' }, [
          String(doc.file_name || 'Untitled'),
          doc.is_private ? ' ' : null,
          doc.is_private ? el('span', { className: 'badge badge-private', text: 'Private' }) : null
        ]),
        el('p', { className: 'small muted', text: 'Category: ' + (doc.doc_category || '—') + (score !== null ? ' · Match score: ' + score : '') }),
        doc.is_private ? el('p', { className: 'small', text: 'On WhatsApp the person must reply YES or 1 before this file is sent.' }) : null,
        el('p', null, [el('a', { href: fileUrl(doc.id), target: '_blank', rel: 'noopener', text: 'Open this file' })])
      ]);
      out.appendChild(box);
    }).catch(function (error) {
      showMessage(out, 'error', 'Test failed', error.message);
    }).then(function () {
      button.disabled = false;
    });
  }

  /* ---------- Activity ---------- */

  var TYPE_LABELS = {
    'webhook': 'WhatsApp request',
    'get-document': 'Dashboard test',
    'upload': 'Upload',
    'archive-document': 'Archived',
    'private-access-audit': 'Private file check',
    'delivery-status': 'Delivery update',
    'unhandled-error': 'Server error'
  };

  function activityResult(entry) {
    if (entry.type === 'webhook' || entry.type === 'get-document') {
      return entry.found
        ? el('span', { className: 'badge badge-ok', text: 'Found' })
        : el('span', { className: 'badge badge-no', text: 'Not found' });
    }
    var text = entry.status || entry.event || entry.reason || entry.message_status || '';
    return text ? el('span', { className: 'badge', text: String(text) }) : el('span', { className: 'muted', text: '—' });
  }

  function activityDetail(entry) {
    var value = entry.query || entry.file_name || entry.error || entry.doc_id || '';
    return String(value);
  }

  function loadActivity() {
    var body = $('activity-body');
    return api('/logs/recent?limit=20').then(function (entries) {
      clear(body);
      var list = Array.isArray(entries) ? entries.slice().reverse() : [];
      if (!list.length) {
        body.appendChild(el('tr', null, [el('td', { colspan: '5', className: 'empty', text: 'No activity yet.' })]));
        return;
      }
      list.forEach(function (entry) {
        entry = entry || {};
        body.appendChild(el('tr', null, [
          el('td', { 'data-label': 'When', className: 'muted', text: formatDate(entry.timestamp) }),
          el('td', { 'data-label': 'Type', text: TYPE_LABELS[entry.type] || String(entry.type || 'event') }),
          el('td', { 'data-label': 'From', className: 'mono', text: entry.sender ? String(entry.sender) : '—' }),
          el('td', { 'data-label': 'Details', className: 'name', text: activityDetail(entry) || '—' }),
          el('td', { 'data-label': 'Result' }, [activityResult(entry)])
        ]));
      });
    }).catch(function (error) {
      clear(body);
      body.appendChild(el('tr', null, [el('td', { colspan: '5', className: 'empty', text: 'Activity could not be loaded: ' + error.message })]));
    });
  }

  /* ---------- Wire up ---------- */

  document.addEventListener('DOMContentLoaded', function () {
    $('upload-form').addEventListener('submit', onUpload);
    $('test-form').addEventListener('submit', onTestQuery);
    $('docs-filter').addEventListener('input', renderDocuments);
    $('docs-refresh').addEventListener('click', function () {
      showMessage($('docs-msg'), 'ok', null, null);
      loadDocuments();
    });
    $('activity-refresh').addEventListener('click', loadActivity);
    $('status-refresh').addEventListener('click', loadStatus);
    loadStatus();
    loadDocuments();
    loadActivity();
  });
})();
"""


def _dashboard_user_block(username: str | None, auth_enabled: bool) -> str:
    if not auth_enabled:
        return '<span class="who">Sign-in is <strong>off</strong></span>'
    name = html.escape(username or "admin")
    return (
        f'<span class="who">Signed in as <strong>{name}</strong></span>'
        '<form method="post" action="/logout">'
        '<button class="btn btn-secondary btn-small" type="submit">Sign out</button>'
        "</form>"
    )


def build_dashboard_page(username: str | None, auth_enabled: bool) -> str:
    """Render the owner dashboard (status, upload, documents, test query, activity)."""
    user_block = _dashboard_user_block(username, auth_enabled)
    body = f"""
<header class="topbar">
  <div class="topbar-inner">
    {_LOGO_SVG}
    <span class="brand-name">InstaRetriv AI</span>
    {user_block}
  </div>
</header>
<main class="shell" id="main">
  <noscript>
    <div class="alert alert-error"><p class="alert-title">JavaScript is required</p>
    <p>Please enable JavaScript to use the dashboard.</p></div>
  </noscript>

  <section aria-labelledby="status-heading" class="card">
    <div class="card-head">
      <h2 id="status-heading">Setup status</h2>
      <button class="btn btn-secondary btn-small" type="button" id="status-refresh">Recheck</button>
    </div>
    <div id="alerts" aria-live="polite"><p class="muted">Checking your setup…</p></div>
    <div class="status-grid" id="status-grid" style="margin-top:12px"></div>
  </section>

  <div class="grid-2">
    <div class="stack">
      <section aria-labelledby="upload-heading" class="card">
        <div class="card-head"><h2 id="upload-heading">Upload a document</h2></div>
        <p class="card-sub">Only the file is required. Category and tags help WhatsApp find it faster.</p>
        <form id="upload-form" class="stacked" novalidate>
          <div class="field">
            <label for="file">File</label>
            <input type="file" id="file" name="file" required aria-describedby="file-hint">
            <span class="hint" id="file-hint">Allowed: PDF, images and Word files.</span>
          </div>
          <div class="field">
            <label for="category">Category <span class="hint">(optional)</span></label>
            <input type="text" id="category" name="doc_category" maxlength="40" autocomplete="off"
                   placeholder="e.g. resume, passport, invoice" aria-describedby="category-hint">
            <span class="hint" id="category-hint">Leave blank and we will guess it from the file name.</span>
          </div>
          <div class="field">
            <label for="tags">Tags <span class="hint">(optional)</span></label>
            <input type="text" id="tags" name="tags" autocomplete="off"
                   placeholder="e.g. pan card, tax, 2024" aria-describedby="tags-hint">
            <span class="hint" id="tags-hint">Separate words with commas. These are the words people will type on WhatsApp.</span>
          </div>
          <div class="check">
            <input type="checkbox" id="is_private" name="is_private" value="true" aria-describedby="private-hint">
            <label for="is_private">Private document
              <span class="hint" id="private-hint">WhatsApp asks for a YES before sending it.</span>
            </label>
          </div>
          <div class="row">
            <button class="btn" type="submit" id="upload-btn">Upload document</button>
          </div>
          <div id="upload-msg" class="msg" aria-live="polite"></div>
        </form>
      </section>

      <section aria-labelledby="test-heading" class="card">
        <div class="card-head"><h2 id="test-heading">Test a WhatsApp message</h2></div>
        <p class="card-sub">See which file the bot would send, without messaging anyone.</p>
        <form id="test-form" class="stacked" role="search">
          <div class="field">
            <label for="test-query">Message</label>
            <div class="row">
              <input type="search" id="test-query" name="query" autocomplete="off" placeholder="send my resume">
              <button class="btn btn-secondary" type="submit" id="test-btn">Test</button>
            </div>
          </div>
          <div id="test-result" class="msg" aria-live="polite"></div>
        </form>
      </section>
    </div>

    <aside aria-labelledby="help-heading" class="card help">
      <div class="card-head"><h2 id="help-heading">How it works</h2></div>
      <ol>
        <li>Upload a file here and give it a clear category or a few tags.</li>
        <li>From an allowed WhatsApp number, send a message such as <span class="kbd">resume</span>
          or <span class="kbd">send my pan card</span>.</li>
        <li>The bot finds the best match and sends the file back on WhatsApp.</li>
      </ol>
      <p><strong>Private files:</strong> the bot first asks for confirmation. Reply
        <span class="kbd">YES</span> or <span class="kbd">1</span> within
        <strong><span id="ttl-seconds">45</span> seconds</strong> to receive the file, or
        <span class="kbd">CANCEL</span> to stop. Sending a different request instead simply starts a new search.</p>
      <p class="muted small">Tip: if a file is not found, add the words you naturally use for it as tags.
        Archived files are never sent.</p>
    </aside>
  </div>

  <section aria-labelledby="docs-heading" class="card">
    <div class="card-head">
      <h2 id="docs-heading">Documents <span class="badge badge-count" id="docs-count">0</span></h2>
      <button class="btn btn-secondary btn-small" type="button" id="docs-refresh">Refresh</button>
    </div>
    <div class="field" style="margin-bottom:12px">
      <label for="docs-filter" class="sr-only">Filter documents</label>
      <input type="search" id="docs-filter" autocomplete="off" placeholder="Filter by name, category or tag">
    </div>
    <div id="docs-msg" class="msg" aria-live="polite" style="margin-bottom:12px"></div>
    <div class="table-wrap">
      <table class="responsive">
        <caption class="sr-only">Active documents</caption>
        <thead>
          <tr><th scope="col">File</th><th scope="col">Category</th><th scope="col">Tags</th>
              <th scope="col">Uploaded</th><th scope="col"><span class="sr-only">Actions</span></th></tr>
        </thead>
        <tbody id="docs-body"><tr><td colspan="5" class="empty">Loading documents…</td></tr></tbody>
      </table>
    </div>
  </section>

  <section aria-labelledby="activity-heading" class="card">
    <div class="card-head">
      <h2 id="activity-heading">Recent activity</h2>
      <button class="btn btn-secondary btn-small" type="button" id="activity-refresh">Refresh</button>
    </div>
    <div class="table-wrap">
      <table class="responsive">
        <caption class="sr-only">Latest 20 events, newest first</caption>
        <thead>
          <tr><th scope="col">When</th><th scope="col">Type</th><th scope="col">From</th>
              <th scope="col">Details</th><th scope="col">Result</th></tr>
        </thead>
        <tbody id="activity-body"><tr><td colspan="5" class="empty">Loading activity…</td></tr></tbody>
      </table>
    </div>
  </section>
</main>
"""
    return _page("InstaRetriv AI · Dashboard", body, extra_css=_DASHBOARD_CSS, script=_DASHBOARD_SCRIPT)
