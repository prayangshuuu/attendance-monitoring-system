from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Attendance Monitoring System"


settings = Settings()
