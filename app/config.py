# app/config.py
from pydantic_settings import BaseSettings
from pydantic import computed_field

class Settings(BaseSettings):
    mysql_user: str
    mysql_password: str
    mysql_host: str
    mysql_db: str
    secret_key: str = "dev-secret-change-me"

    @computed_field
    @property
    def sqlalchemy_url(self) -> str:
        # mysqlclient (MySQLdb) driver
        return f"mysql+mysqldb://{self.mysql_user}:{self.mysql_password}@{self.mysql_host}/{self.mysql_db}?charset=utf8mb4"

    # in app/config.py (within Settings)
    session_secret: str | None = None  # optional

    @property
    def secret_key(self) -> str:
        # keep using SECRET_KEY if set; otherwise fall back to SESSION_SECRET
        return (getattr(self, "_secret_key", None)  # if you had a field named secret_key
                or self.session_secret)

    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()
