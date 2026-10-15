from time import perf_counter
from uuid import uuid4

from app.schemas import TraceEvent


class TraceService:
    def __init__(self) -> None:
        self._events_by_session: dict[str, list[TraceEvent]] = {}

    def add_event(
        self,
        session_id: str,
        step: str,
        agent: str,
        event_type: str,
        input_summary: str,
        output_summary: str,
        latency_ms: int = 0,
        token_input: int = 0,
        token_output: int = 0,
        error: str | None = None,
    ) -> TraceEvent:
        event = TraceEvent(
            trace_id=f"trace_{uuid4().hex[:10]}",
            session_id=session_id,
            step=step,
            agent=agent,
            event_type=event_type,
            input_summary=input_summary,
            output_summary=output_summary,
            latency_ms=latency_ms,
            token_input=token_input,
            token_output=token_output,
            error=error,
        )
        self._events_by_session.setdefault(session_id, []).append(event)
        return event

    def list_events(self, session_id: str) -> list[TraceEvent]:
        return self._events_by_session.get(session_id, [])


class trace_step:
    def __init__(self) -> None:
        self.started = 0.0

    def __enter__(self) -> "trace_step":
        self.started = perf_counter()
        return self

    def latency_ms(self) -> int:
        return int((perf_counter() - self.started) * 1000)

    def __exit__(self, *_args: object) -> None:
        return None
