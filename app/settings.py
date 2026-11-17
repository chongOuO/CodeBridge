"""應用程式設定：以環境變數控制部署環境的輸出行為。"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """集中管理設定；本地預設保留內容端點的報告寫檔行為。"""

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    content_report_write_files: bool = True


def get_settings() -> Settings:
    """提供 API 依賴使用的應用程式設定。"""
    return Settings()
