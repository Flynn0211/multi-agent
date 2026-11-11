from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / '.env', extra='ignore')
    opencode_server_url: str = 'http://127.0.0.1:4096'
    opencode_server_password: str = ''
    opencode_server_username: str = 'opencode'
    opencode_server_token: str = ''
    opencode_agent: str = 'build'
    opencode_model: str = ''
    opencode_auto_fallback: bool = True
    opencode_fallback_models: str = ''
    opencode_max_fallbacks: int = 4
    data_dir: Path = ROOT / 'data'
    max_revisions: int = 3
    execution_timeout: int = 3600


settings = Settings()
