"""全局配置：所有密钥走环境变量 / .env，不入代码库。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---- 数据库 ----
    db_host: str = "localhost"
    db_port: int = 3306
    db_user: str = "root"
    db_password: str = "root"
    db_name: str = "ai_code_agent"

    # ---- Redis ----
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0
    redis_ttl: int = 3600

    # ---- 服务 ----
    server_port: int = 8123
    session_secret: str = "dev-only-secret-change-me"
    session_max_age: int = 2592000  # 30 天，与 Java 版 cookie max-age 一致

    # ---- LLM ----
    llm_base_url: str = "https://api.deepseek.com"
    llm_api_key: str = ""
    llm_chat_model: str = "deepseek-chat"
    llm_reasoning_model: str = "deepseek-reasoner"
    llm_routing_model: str = "deepseek-chat"
    llm_max_tokens: int = 8192
    llm_reasoning_max_tokens: int = 32768
    llm_reasoning_temperature: float = 0.1
    llm_max_retries: int = 3

    # ---- 可降级的附属能力 ----
    pexels_api_key: str = ""
    dashscope_api_key: str = ""
    dashscope_image_model: str = "wan2.2-t2i-flash"
    cos_host: str = ""
    cos_secret_id: str = ""
    cos_secret_key: str = ""
    cos_region: str = ""
    cos_bucket: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"mysql+aiomysql://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def cos_enabled(self) -> bool:
        return bool(self.cos_secret_id and self.cos_secret_key and self.cos_bucket)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
