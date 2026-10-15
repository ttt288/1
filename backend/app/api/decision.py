from functools import lru_cache

from fastapi import APIRouter, HTTPException

from app.core.config import get_settings
from app.schemas import DecisionAnswerRequest, DecisionStartRequest, MemoryDecisionRequest
from app.services.decision_service import DecisionService

router = APIRouter(tags=["decision"])


@lru_cache
def get_decision_service() -> DecisionService:
    return DecisionService(get_settings())


@router.post("/decision/start")
def start_decision(request: DecisionStartRequest):
    service = get_decision_service()
    return service.start(
        query=request.query,
        user_id=request.user_id,
        use_external_refresh=request.use_external_refresh,
    )


@router.post("/decision/answer")
def answer_questions(request: DecisionAnswerRequest):
    service = get_decision_service()
    try:
        return service.answer(
            session_id=request.session_id,
            user_id=request.user_id,
            answers=request.answers,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/traces/{session_id}")
def list_traces(session_id: str):
    service = get_decision_service()
    return service.list_traces(session_id)


@router.get("/memory/{user_id}")
def list_memories(user_id: str = "demo_user"):
    service = get_decision_service()
    return service.list_memories(user_id)


@router.post("/memory/decision")
def apply_memory_decision(request: MemoryDecisionRequest):
    service = get_decision_service()
    return service.apply_memory_decision(request.memory, request.action)
