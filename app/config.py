from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "InstaRetriv AI"
    app_env: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000

    authorized_senders: str = ""

    upload_dir: str = "uploads"
    metadata_file: str = "data/metadata.json"

    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = "whatsapp:+14155238886"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def authorized_senders_list(self) -> list[str]:
        if not self.authorized_senders.strip():
            return []
        return [item.strip() for item in self.authorized_senders.split(",") if item.strip()]


settings = Settings()
