import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from app.schemas import MemoryItem, UserProfile


CONFIDENCE_THRESHOLD = 0.55
DECAY_HALF_LIFE_DAYS = {
    "communication_preference": 120,
    "inferred_preference": 45,
    "explicit_fact": 365,
}
DEFAULT_TTL_DAYS = {
    "communication_preference": 180,
    "inferred_preference": 60,
    "explicit_fact": 365,
}


class MemoryService:
    def __init__(self, data_dir: Path) -> None:
        self.memory_dir = data_dir / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def list_memories(self, user_id: str) -> list[MemoryItem]:
        path = self._memory_path(user_id)
        if not path.exists():
            return []
        raw_items = json.loads(path.read_text(encoding="utf-8"))
        memories = [MemoryItem.model_validate(item) for item in raw_items]
        normalized, changed = self._normalize_memories(memories)
        if changed:
            self._write_memories(user_id, normalized)
        return [
            memory
            for memory in normalized
            if memory.status == "active" and memory.confidence >= CONFIDENCE_THRESHOLD
        ]

    def propose_updates(self, user_profile: UserProfile) -> list[MemoryItem]:
        proposals: list[MemoryItem] = []
        communication = user_profile.communication_profile
        if communication.get("prefers_table") is True:
            proposals.append(
                MemoryItem(
                    id=f"mem_{uuid4().hex[:10]}",
                    user_id=user_profile.user_id,
                    memory_type="communication_preference",
                    domain="global",
                    key="prefers_table",
                    value=True,
                    confidence=0.82,
                    source="conversation",
                    evidence="用户本次选择或系统识别出更适合表格对比展示。",
                    expires_at=self._default_expires_at("communication_preference"),
                    requires_confirmation=True,
                )
            )

        tone = communication.get("tone")
        if tone and tone != "warm_and_practical":
            proposals.append(
                MemoryItem(
                    id=f"mem_{uuid4().hex[:10]}",
                    user_id=user_profile.user_id,
                    memory_type="communication_preference",
                    domain="global",
                    key="response_tone",
                    value=tone,
                    confidence=0.88,
                    source="conversation",
                    evidence="用户本次选择了回答语气偏好，该偏好只用于表达方式，不参与商品安全和排序判断。",
                    expires_at=self._default_expires_at("communication_preference"),
                    requires_confirmation=True,
                )
            )
        return proposals

    def save_memory(self, memory: MemoryItem) -> MemoryItem:
        existing = self._read_all_memories(memory.user_id)
        memory.requires_confirmation = False
        memory.status = "active"
        memory.last_updated = self._now()
        memory.last_accessed = self._now()
        memory.access_count = max(memory.access_count, 1)
        if memory.expires_at is None:
            memory.expires_at = self._default_expires_at(memory.memory_type)

        merged: list[MemoryItem] = []
        for item in existing:
            if item.domain == memory.domain and item.key == memory.key:
                if self._same_value(item.value, memory.value):
                    memory.id = item.id
                    memory.created_at = item.created_at
                    memory.confidence = min(0.99, max(item.confidence, memory.confidence) + 0.03)
                    memory.access_count = item.access_count + 1
                    continue
                item.status = "conflicted"
                item.conflict_of = memory.id
                item.confidence = round(item.confidence * 0.5, 3)
                item.last_updated = self._now()
                memory.conflict_of = item.id
                memory.evidence = f"{memory.evidence}；与旧记忆 {item.key}={item.value} 冲突，已替换旧值。"
            merged.append(item)
        memory.requires_confirmation = False
        merged.append(memory)
        normalized, _ = self._normalize_memories(merged)
        self._write_memories(memory.user_id, normalized)
        return memory

    def apply_memory_decision(self, memory: MemoryItem, action: str) -> dict[str, str]:
        if action == "remember":
            self.save_memory(memory)
            return {"status": "remembered", "memory_id": memory.id}
        if action == "session_only":
            return {"status": "session_only", "memory_id": memory.id}
        return {"status": "rejected", "memory_id": memory.id}

    def merge_into_profile(self, user_profile: UserProfile) -> UserProfile:
        memories = self.list_memories(user_profile.user_id)
        touched = False
        for memory in memories:
            if memory.domain not in ["global", user_profile.scenario_profile.get("domain")]:
                continue
            memory.last_accessed = self._now()
            memory.access_count += 1
            touched = True
            if memory.key == "prefers_table":
                user_profile.communication_profile["prefers_table"] = bool(memory.value)
            elif memory.key == "response_tone":
                user_profile.communication_profile["tone"] = str(memory.value)
        if touched:
            all_memories = self._read_all_memories(user_profile.user_id)
            touched_by_id = {memory.id: memory for memory in memories}
            merged = [touched_by_id.get(memory.id, memory) for memory in all_memories]
            self._write_memories(user_profile.user_id, merged)
        return user_profile

    def _read_all_memories(self, user_id: str) -> list[MemoryItem]:
        path = self._memory_path(user_id)
        if not path.exists():
            return []
        raw_items = json.loads(path.read_text(encoding="utf-8"))
        return [MemoryItem.model_validate(item) for item in raw_items]

    def _normalize_memories(self, memories: list[MemoryItem]) -> tuple[list[MemoryItem], bool]:
        now = self._now()
        changed = False
        normalized: list[MemoryItem] = []
        for memory in memories:
            if memory.expires_at is None:
                memory.expires_at = self._default_expires_at(memory.memory_type, memory.created_at)
                changed = True
            if memory.status == "active" and memory.expires_at <= now:
                memory.status = "expired"
                changed = True
            if memory.status == "active":
                decayed = self._decayed_confidence(memory, now)
                if decayed < memory.confidence:
                    memory.confidence = decayed
                    changed = True
                if memory.confidence < CONFIDENCE_THRESHOLD:
                    memory.status = "expired"
                    changed = True
            normalized.append(memory)
        return normalized, changed

    def _decayed_confidence(self, memory: MemoryItem, now: datetime) -> float:
        half_life = DECAY_HALF_LIFE_DAYS.get(memory.memory_type, 90)
        anchor = memory.last_accessed or memory.last_updated or memory.created_at
        days = max((now - anchor).total_seconds() / 86400, 0)
        decayed = memory.confidence * (0.5 ** (days / half_life))
        return round(max(0, min(1, decayed)), 3)

    def _default_expires_at(self, memory_type: str, created_at: datetime | None = None) -> datetime:
        base = created_at or self._now()
        return base + timedelta(days=DEFAULT_TTL_DAYS.get(memory_type, 90))

    def _same_value(self, left, right) -> bool:
        return json.dumps(left, ensure_ascii=False, sort_keys=True) == json.dumps(
            right, ensure_ascii=False, sort_keys=True
        )

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _memory_path(self, user_id: str) -> Path:
        safe_user_id = "".join(ch for ch in user_id if ch.isalnum() or ch in ["_", "-"])
        return self.memory_dir / f"{safe_user_id}.json"

    def _write_memories(self, user_id: str, memories: list[MemoryItem]) -> None:
        path = self._memory_path(user_id)
        payload = [item.model_dump(mode="json") for item in memories]
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
