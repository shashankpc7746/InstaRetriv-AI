def build_upload_page() -> str:
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>InstaRetriv AI - Upload Documents</title>
        <style>
            :root {
                --bg: #f3efe7;
                --surface: rgba(255, 255, 255, 0.92);
                --surface-strong: #ffffff;
                --text: #172033;
                --muted: #667085;
                --border: rgba(23, 32, 51, 0.12);
                --brand: #0f62fe;
                --brand-strong: #0b4ed0;
                --accent: #f97316;
                --success: #0f9d58;
                --soft-blue: #e8f0ff;
                --soft-green: #e9f7ee;
                --soft-amber: #fff4e5;
                --shadow: 0 20px 50px rgba(15, 23, 42, 0.12);
            }
            * { box-sizing: border-box; }
            body {
                margin: 0;
                min-height: 100vh;
                font-family: "Trebuchet MS", "Segoe UI", sans-serif;
                color: var(--text);
                background:
                    radial-gradient(circle at top left, rgba(15, 98, 254, 0.16), transparent 28%),
                    radial-gradient(circle at top right, rgba(249, 115, 22, 0.12), transparent 26%),
                    linear-gradient(180deg, #fcfbf7 0%, #f6f2ea 100%);
                padding: 28px 18px 40px;
            }
            .shell {
                max-width: 1180px;
                margin: 0 auto;
            }
            .hero {
                display: grid;
                grid-template-columns: 1.3fr 0.9fr;
                gap: 18px;
                align-items: stretch;
                margin-bottom: 20px;
            }
            .hero-main, .hero-side, .card, .info, .profile-box {
                background: var(--surface);
                border: 1px solid var(--border);
                border-radius: 22px;
                box-shadow: var(--shadow);
                backdrop-filter: blur(10px);
            }
            .hero-main {
                padding: 26px;
            }
            .hero-side {
                padding: 22px;
                display: grid;
                gap: 14px;
            }
            .eyebrow {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 7px 12px;
                border-radius: 999px;
                background: rgba(15, 98, 254, 0.08);
                color: var(--brand-strong);
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.02em;
                text-transform: uppercase;
            }
            h1 {
                margin: 14px 0 12px;
                font-size: clamp(2rem, 4vw, 3.2rem);
                line-height: 1.03;
                letter-spacing: -0.03em;
            }
            .hero-copy {
                color: var(--muted);
                font-size: 1rem;
                line-height: 1.65;
                max-width: 62ch;
            }
            .chip-row {
                display: flex;
                flex-wrap: wrap;
                gap: 10px;
                margin-top: 18px;
            }
            .chip {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                padding: 10px 14px;
                border-radius: 999px;
                font-size: 13px;
                font-weight: 700;
                background: #fff;
                border: 1px solid var(--border);
            }
            .chip.blue { background: var(--soft-blue); }
            .chip.green { background: var(--soft-green); }
            .chip.amber { background: var(--soft-amber); }
            .stat-grid {
                display: grid;
                gap: 12px;
            }
            .stat {
                padding: 16px;
                border-radius: 18px;
                background: linear-gradient(180deg, rgba(255,255,255,0.95), rgba(255,255,255,0.78));
                border: 1px solid rgba(23, 32, 51, 0.08);
            }
            .stat strong { display: block; font-size: 1rem; margin-bottom: 5px; }
            .stat span { color: var(--muted); font-size: 0.92rem; line-height: 1.45; }
            .content-grid {
                display: grid;
                grid-template-columns: 1fr 0.92fr;
                gap: 18px;
                align-items: start;
            }
            .stack { display: grid; gap: 18px; }
            .card {
                padding: 22px;
            }
            .card h2 {
                margin: 0 0 14px;
                font-size: 1.2rem;
                letter-spacing: -0.02em;
            }
            .card p,
            .card li,
            .hint,
            .small {
                color: var(--muted);
                line-height: 1.6;
            }
            form { display: grid; gap: 12px; }
            input, textarea {
                width: 100%;
                padding: 13px 14px;
                border: 1px solid rgba(23, 32, 51, 0.14);
                border-radius: 14px;
                background: rgba(255,255,255,0.96);
                color: var(--text);
                box-sizing: border-box;
                font: inherit;
                transition: border-color 0.18s ease, box-shadow 0.18s ease, transform 0.18s ease;
            }
            input:focus, textarea:focus {
                outline: none;
                border-color: rgba(15, 98, 254, 0.5);
                box-shadow: 0 0 0 4px rgba(15, 98, 254, 0.12);
            }
            textarea { min-height: 110px; resize: vertical; }
            .button-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
            button {
                background: linear-gradient(135deg, var(--brand), var(--brand-strong));
                color: white;
                padding: 13px 18px;
                border: none;
                border-radius: 14px;
                cursor: pointer;
                font-size: 15px;
                font-weight: 700;
                box-shadow: 0 14px 24px rgba(15, 98, 254, 0.24);
                transition: transform 0.18s ease, box-shadow 0.18s ease, filter 0.18s ease;
            }
            button:hover { transform: translateY(-1px); filter: brightness(1.02); box-shadow: 0 18px 28px rgba(15, 98, 254, 0.28); }
            .ghost-link {
                display: inline-flex;
                align-items: center;
                gap: 8px;
                color: var(--brand-strong);
                text-decoration: none;
                font-weight: 700;
            }
            .ghost-link:hover { text-decoration: underline; }
            .section { margin: 0; }
            .info {
                padding: 18px;
                background: linear-gradient(180deg, rgba(232, 240, 255, 0.98), rgba(232, 240, 255, 0.78));
            }
            .info ul { margin: 10px 0 0 18px; padding: 0; }
            .info li { margin-bottom: 8px; }
            .checkbox-row { display: flex; align-items: center; gap: 10px; margin-top: 4px; }
            .checkbox-row input[type='checkbox'] { width: auto; margin: 0; transform: scale(1.05); }
            .hint {
                font-size: 0.93rem;
                margin-top: 0;
            }
            .profile-box {
                padding: 20px;
                background: linear-gradient(180deg, rgba(233, 247, 238, 0.98), rgba(233, 247, 238, 0.82));
            }
            .profile-box .small { margin-top: 4px; }
            code {
                background: rgba(15, 32, 51, 0.08);
                padding: 2px 7px;
                border-radius: 999px;
                font-size: 0.92em;
            }
            .form-note {
                font-size: 0.9rem;
                color: var(--muted);
                margin-top: -3px;
            }
            .badge {
                display: inline-flex;
                align-items: center;
                padding: 6px 10px;
                border-radius: 999px;
                background: rgba(15, 157, 88, 0.12);
                color: #12794a;
                font-size: 12px;
                font-weight: 800;
                letter-spacing: 0.02em;
                text-transform: uppercase;
            }
            .mini-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px;
            }
            .mini-card {
                padding: 14px;
                border-radius: 16px;
                background: rgba(255,255,255,0.86);
                border: 1px solid rgba(23, 32, 51, 0.08);
            }
            .mini-card strong { display: block; margin-bottom: 4px; }
            .mini-card span { color: var(--muted); font-size: 13px; line-height: 1.45; }
            @media (max-width: 920px) {
                .hero, .content-grid { grid-template-columns: 1fr; }
            }
            @media (max-width: 640px) {
                body { padding: 16px 12px 26px; }
                .hero-main, .hero-side, .card, .profile-box, .info { border-radius: 18px; padding: 18px; }
                .mini-grid { grid-template-columns: 1fr; }
                .button-row { flex-direction: column; align-items: stretch; }
                button { width: 100%; }
            }
        </style>
    </head>
    <body>
        <div class="shell">
            <div class="hero">
                <div class="hero-main">
                    <span class="eyebrow">InstaRetriv AI Workspace</span>
                    <h1>Document vaulting with WhatsApp retrieval, built for speed.</h1>
                    <p class="hero-copy">
                        Upload files, keep one global private access code, and let the app infer smart tags and categories when you need to move fast.
                    </p>
                    <div class="chip-row">
                        <span class="chip blue">Global private code</span>
                        <span class="chip green">Auto tags from filename</span>
                        <span class="chip amber">Private file toggle per upload</span>
                    </div>
                </div>
                <div class="hero-side">
                    <div class="stat-grid">
                        <div class="stat">
                            <strong>Private access</strong>
                            <span>Set a single profile-level passcode once and reuse it for all private documents.</span>
                        </div>
                        <div class="stat">
                            <strong>Fast upload</strong>
                            <span>Leave category and tags blank when needed. The app fills sensible metadata automatically.</span>
                        </div>
                    </div>
                    <a class="ghost-link" href="/health">View health check</a>
                </div>
            </div>

            <div class="content-grid">
                <div class="stack">
                    <div class="profile-box">
                        <div class="badge">Profile Security</div>
                        <h2>Set one private access code for all private documents.</h2>
                        <p class="small">This replaces the old per-file password pattern. Change it here whenever you need to rotate access.</p>
                        <form method="post" action="/profile/private-access-code">
                            <input type="password" name="private_access_code" minlength="4" placeholder="New private access code" required>
                            <input type="password" name="confirm_private_access_code" minlength="4" placeholder="Confirm private access code" required>
                            <div class="button-row">
                                <button type="submit">Set or Change Private Access Code</button>
                            </div>
                        </form>
                    </div>

                    <div class="card">
                        <span class="badge">Upload</span>
                        <h2>Upload Document</h2>
                        <form method="post" action="/upload" enctype="multipart/form-data">
                            <input type="file" name="file" required accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,.webp">
                            <input type="text" name="doc_category" placeholder="Category (optional: e.g., resume, certificate)">
                            <textarea name="tags" placeholder="Tags (optional, comma-separated: e.g., resume, pdf, work)"></textarea>

                            <div class="checkbox-row">
                                <input type="checkbox" id="is_private" name="is_private" value="true">
                                <label for="is_private"><strong>Mark as private document</strong></label>
                            </div>
                            <p class="form-note">Private docs use your single profile-level code in WhatsApp retrieval, for example <code>code 1234</code>.</p>
                            <p class="form-note">Phase 13: Tags are auto-enriched from filename and category can be auto-suggested if you choose a generic category.</p>

                            <div class="button-row">
                                <button type="submit">Upload Document</button>
                            </div>
                        </form>
                    </div>
                </div>

                <div class="stack">
                    <div class="info">
                        <h2>Why the new upload flow helps</h2>
                        <div class="mini-grid">
                            <div class="mini-card">
                                <strong>No mandatory metadata</strong>
                                <span>Category and tags are now optional when you are uploading in a hurry.</span>
                            </div>
                            <div class="mini-card">
                                <strong>Less manual typing</strong>
                                <span>File names and common keywords become useful tags automatically.</span>
                            </div>
                            <div class="mini-card">
                                <strong>One private code</strong>
                                <span>All private files share the same profile password, so rotation is simple.</span>
                            </div>
                            <div class="mini-card">
                                <strong>WhatsApp friendly</strong>
                                <span>The bot can respond with the matched file or a clear next-step message.</span>
                            </div>
                        </div>
                    </div>

                    <div class="info">
                        <strong>📝 Tips</strong>
                        <ul>
                            <li>Use clear tags if you know them, but leaving them empty is okay.</li>
                            <li>Examples: "resume", "cover letter", "pan card", "aadhar", "invoice".</li>
                            <li>Use the private checkbox only for files that need restricted access.</li>
                        </ul>
                    </div>

                    <div class="info">
                        <h2>🤖 WhatsApp Integration</h2>
                        <p>Send a WhatsApp message to your Twilio Sandbox with a query like:</p>
                        <ul>
                            <li>"send my resume"</li>
                            <li>"pan card"</li>
                            <li>"aadhar certificate"</li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>
        <script>
            const privateToggle = document.getElementById('is_private');
            if (privateToggle) {
                privateToggle.addEventListener('change', function () {
                    if (privateToggle.checked) {
                        alert('This file will require your global private access code during retrieval.');
                    }
                });
            }
        </script>
    </body>
    </html>
    """
