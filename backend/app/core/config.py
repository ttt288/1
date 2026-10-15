from functools import lru_cache
import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv()


class Settings(BaseModel):
    demo_user_id: str = "demo_user"
    project_root: Path = Path(__file__).resolve().parents[3]
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5-nano")
    llm_enabled: bool = os.getenv("LLM_ENABLED", "false").lower() == "true"
    llm_max_input_products: int = int(os.getenv("LLM_MAX_INPUT_PRODUCTS", "8"))
    llm_timeout_seconds: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "25"))
    llm_max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "1"))
    llm_max_output_tokens: int = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", "300"))
    llm_query_understanding_enabled: bool = (
        os.getenv("LLM_QUERY_UNDERSTANDING_ENABLED", "false").lower() == "true"
    )
    llm_web_search_enabled: bool = os.getenv("LLM_WEB_SEARCH_ENABLED", "true").lower() == "true"

    @property
    def data_dir(self) -> Path:
        return self.project_root / "data"


@lru_cache
def get_settings() -> Settings:
    return Settings()
