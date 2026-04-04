from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "InstaRetriv AI"
    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000

    authorized_senders: str = ""

    upload_dir: str = "uploads"
    storage_backend: str = "local"
    cloudinary_cloud_name: str = ""
    cloudinary_api_key: str = ""
    cloudinary_api_secret: str = ""
    metadata_file: str = "data/metadata.json"
    request_log_file: str = "data/request_logs.json"

    metadata_backend: str = "json"
    mongodb_uri: str = ""
    mongodb_database: str = "instaretriv_ai"
    mongodb_collection: str = "documents"

    allowed_extensions: str = "pdf,png,jpg,jpeg,webp,doc,docx"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_secondary_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"
    twilio_send_retries: int = 2
    public_base_url: str = ""
    require_twilio_signature: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def authorized_senders_list(self) -> list[str]:
        if not self.authorized_senders.strip():
            return []
        return [item.strip() for item in self.authorized_senders.split(",") if item.strip()]

    @property
    def allowed_extensions_list(self) -> list[str]:
        if not self.allowed_extensions.strip():
            return []
        return [item.strip().lower() for item in self.allowed_extensions.split(",") if item.strip()]

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


settings = Settings()
