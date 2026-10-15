from fastapi import APIRouter

from app.core.config import get_settings
from app.agents import LLMAdapter

router = APIRouter(tags=["health"])


@router.get("/health")
def health_check() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/llm/status")
def llm_status() -> dict[str, str | bool]:
    settings = get_settings()
    adapter = LLMAdapter(settings)
    sdk_available = adapter.sdk_available()
    runnable = bool(settings.llm_enabled and settings.openai_api_key and sdk_available)
    return {
        "enabled": runnable,
        "configured": bool(settings.openai_api_key),
        "sdk_available": sdk_available,
        "model": settings.openai_model,
        "mode": "llm" if runnable else "rules",
        "runtime_status": "configured" if runnable else "not_configured",
    }
