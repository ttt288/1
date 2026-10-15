from functools import lru_cache

from fastapi import APIRouter

from app.core.config import get_settings
from app.evaluation import EvaluationService

router = APIRouter(tags=["evaluation"])


@lru_cache
def get_evaluation_service() -> EvaluationService:
    return EvaluationService(get_settings())


@router.post("/evaluation/run")
def run_evaluation():
    return get_evaluation_service().run()
