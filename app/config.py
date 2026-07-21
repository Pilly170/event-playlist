from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    spotify_redirect_uri: str = ""
    token_encryption_key: str = ""
    session_secret_key: str = ""
    database_path: str = "./data/app.db"
    domain: str = "localhost"


settings = Settings()
