# app/config.py
from pydantic_settings import BaseSettings
from pydantic import computed_field


class Settings(BaseSettings):
    mysql_user: str
    mysql_password: str
    mysql_host: str
    mysql_db: str

    # App secrets
    secret_key: str | None = None  # Primary secret
    session_secret: str | None = None  # Optional legacy/alt secret

    @computed_field
    @property
    def sqlalchemy_url(self) -> str:
        # Use mysqlclient (MySQLdb) driver
        return (
            f"mysql+mysqldb://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}/{self.mysql_db}?charset=utf8mb4"
        )

    @property
    def session_key(self) -> str:
        """
        Unified session secret.
        - If SECRET_KEY is set, use it.
        - Otherwise fall back to SESSION_SECRET.
        - If neither is set, fall back to a dev default.
        """
        return (
            self.secret_key
            or self.session_secret
            or "dev-secret-change-me"
        )

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
