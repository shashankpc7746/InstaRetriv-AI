import re

from pydantic_settings import BaseSettings, SettingsConfigDict


def normalize_phone(value: str) -> str:
    """Normalize a WhatsApp/phone identifier to E.164-ish form: '+<digits>'.

    'whatsapp:+91 98765-43210' -> '+919876543210'; '' -> ''.
    """
    raw = (value or "").strip()
    if raw.lower().startswith("whatsapp:"):
        raw = raw.split(":", 1)[1]
    digits = re.sub(r"\D", "", raw)
    return f"+{digits}" if digits else ""


class Settings(BaseSettings):
    app_name: str = "InstaRetriv AI"
    # development | test | production
    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000

    # Secret used to sign dashboard sessions and short-lived file links.
    # REQUIRED in production. If empty, a random per-process secret is used
    # (sessions and file links then stop working after a restart).
    app_secret_key: str = ""

    # Comma-separated WhatsApp numbers allowed to use the bot, e.g. whatsapp:+911234567890.
    # Empty list: allowed in development, but in production every sender is
    # rejected unless ALLOW_ANY_SENDER=true.
    authorized_senders: str = ""
    allow_any_sender: bool = False

    upload_dir: str = "uploads"
    storage_backend: str = "local"
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    metadata_file: str = "data/metadata.json"
    request_log_file: str = "data/request_logs.json"
    request_log_max_entries: int = 2000

    metadata_backend: str = "json"
    mongodb_uri: str = ""
    mongodb_database: str = "instaretriv_ai"
    mongodb_collection: str = "documents"
    mongodb_request_log_collection: str = "request_logs"
    mongodb_timeout_ms: int = 5000
    # How often to retry connecting to an unavailable metadata store.
    metadata_store_retry_seconds: int = 30

    allowed_extensions: str = "pdf,png,jpg,jpeg,webp,doc,docx"
    # WhatsApp media limit is 16 MB.
    max_upload_mb: int = 15

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_secondary_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    twilio_send_retries: int = 2
    public_base_url: str = ""
    # Always enforced when APP_ENV=production, whatever this flag says.
    require_twilio_signature: bool = False

    dashboard_auth_enabled: bool = True
    dashboard_username: str = "admin"
    # No default password: login stays disabled until DASHBOARD_PASSWORD is set.
    dashboard_password: str = ""
    dashboard_session_ttl_minutes: int = 720
    login_max_attempts: int = 5
    login_lockout_minutes: int = 15

    # API docs (/docs, /redoc, /openapi.json). Default: on outside production, off in production.
    enable_api_docs: bool | None = None

    # Lifetime of signed /files links handed to Twilio and the dashboard.
    file_url_ttl_seconds: int = 900

    private_confirmation_ttl_seconds: int = 45
    private_confirmation_yes_keywords: str = "yes,y,ok,confirm,1,haan,ha"
    private_confirmation_cancel_keywords: str = "no,n,cancel,stop,0"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in {"production", "prod"}

    @property
    def api_docs_enabled(self) -> bool:
        if self.enable_api_docs is None:
            return not self.is_production
        return self.enable_api_docs

    @property
    def twilio_signature_required(self) -> bool:
        return self.require_twilio_signature or self.is_production

    @property
    def authorized_senders_list(self) -> list[str]:
        """Normalized ('+<digits>') list of authorized senders."""
        result: list[str] = []
        for item in self.authorized_senders.split(","):
            normalized = normalize_phone(item)
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def is_sender_authorized(self, sender: str) -> bool:
        normalized = normalize_phone(sender)
        if not normalized:
            return False
        allowed = self.authorized_senders_list
        if allowed:
            return normalized in allowed
        # No allowlist configured.
        return self.allow_any_sender or not self.is_production

    @property
    def allowed_extensions_list(self) -> list[str]:
        if not self.allowed_extensions.strip():
            return []
        return [item.strip().lower().lstrip(".") for item in self.allowed_extensions.split(",") if item.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return max(1, self.max_upload_mb) * 1024 * 1024

    @property
    def use_mongo_metadata_backend(self) -> bool:
        return self.metadata_backend.strip().lower() == "mongo"

    @property
    def use_cloudinary_storage_backend(self) -> bool:
        return self.storage_backend.strip().lower() == "cloudinary"

    @property
    def cloudinary_configured(self) -> bool:
        return bool(
            self.cloudinary_cloud_name.strip()
            and self.cloudinary_api_key.strip()
            and self.cloudinary_api_secret.strip()
        )

    @property
    def dashboard_login_available(self) -> bool:
        return bool(self.dashboard_password)

    @property
    def effective_private_confirmation_ttl_seconds(self) -> int:
        """Single source of truth for the confirmation window (prompt text AND enforcement)."""
        return max(15, self.private_confirmation_ttl_seconds)

    @property
    def private_confirmation_yes_keywords_list(self) -> list[str]:
        items = [item.strip().lower() for item in self.private_confirmation_yes_keywords.split(",") if item.strip()]
        return items or ["yes"]

    @property
    def private_confirmation_cancel_keywords_list(self) -> list[str]:
        items = [item.strip().lower() for item in self.private_confirmation_cancel_keywords.split(",") if item.strip()]
        return items or ["cancel"]


settings = Settings()
