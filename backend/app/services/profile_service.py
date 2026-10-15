from typing import Any

from app.schemas import UserProfile


class ProfileService:
    def build_profile(self, user_id: str, domain: str, answers: dict[str, Any]) -> UserProfile:
        scenario_profile: dict[str, Any] = {"domain": domain, **answers}
        decision_profile: dict[str, Any] = {
            "budget_sensitivity": self._budget_sensitivity(answers),
            "risk_sensitivity": self._risk_sensitivity(domain, answers),
            "priority": answers.get("priority") or answers.get("health_goal"),
        }
        communication_profile: dict[str, Any] = {
            "prefers_table": answers.get("explanation_level") in [None, "表格对比"],
            "detail_level": self._detail_level(answers),
            "tone": self._tone(answers),
        }
        return UserProfile(
            user_id=user_id,
            scenario_profile=scenario_profile,
            decision_profile=decision_profile,
            communication_profile=communication_profile,
        )

    def _budget_sensitivity(self, answers: dict[str, Any]) -> float:
        budget = str(answers.get("budget_range", ""))
        if "预算优先" in budget:
            return 0.85
        if "预算不敏感" in budget:
            return 0.35
        if "以内" in budget or "100" in budget or "150" in budget:
            return 0.85
        if "以上" in budget:
            return 0.35
        return 0.6

    def _risk_sensitivity(self, domain: str, answers: dict[str, Any]) -> float:
        if domain == "supplement" and answers.get("medical_context") in ["有", "不确定"]:
            return 0.95
        if domain == "pet_food" and answers.get("digestive_issue") in ["经常", "偶尔"]:
            return 0.85
        return 0.6

    def _detail_level(self, answers: dict[str, Any]) -> str:
        explanation = answers.get("explanation_level")
        if explanation == "专业详细":
            return "high"
        if explanation == "简单直接":
            return "low"
        return "medium"

    def _tone(self, answers: dict[str, Any]) -> str:
        tone = answers.get("response_tone")
        if tone == "温柔可爱":
            return "cute_supportive"
        if tone == "直接理性":
            return "direct_rational"
        if tone == "专业详细":
            return "professional_detailed"
        if tone == "轻松鼓励":
            return "relaxed_encouraging"
        return "warm_and_practical"
