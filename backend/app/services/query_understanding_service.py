from typing import Any

from app.agents import LLMAdapter
from app.schemas.ai import AIQueryUnderstanding


NULL_LIKE_VALUES = {"", "null", "none", "undefined", "unknown", "未知", "未提及"}


class QueryUnderstandingService:
    """Low-cost query understanding before LLM extraction is added."""

    def __init__(self, llm_adapter: LLMAdapter | None = None) -> None:
        self.llm_adapter = llm_adapter

    def infer_answers(self, domain: str, query: str, scenario: str | None) -> dict[str, Any]:
        rule_answers = self._infer_answers_by_rules(domain, query, scenario)
        llm_answers = self._infer_answers_by_llm(domain, query, scenario, rule_answers)
        return self.normalize_answers({**llm_answers, **rule_answers})

    def normalize_answers(self, answers: dict[str, Any], questions: list[Any] | None = None) -> dict[str, Any]:
        """Keep only meaningful inferred values and validate answers for known questions."""
        question_by_id = {question.id: question for question in questions or []}
        normalized: dict[str, Any] = {}

        for key, value in (answers or {}).items():
            if not self._has_meaningful_value(value):
                continue

            question = question_by_id.get(key)
            if question and question.options:
                if isinstance(value, list):
                    value = [item for item in value if item in question.options]
                    if not value:
                        continue
                elif value not in question.options:
                    continue

            normalized[key] = value

        return normalized

    @staticmethod
    def _has_meaningful_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return value.strip().lower() not in NULL_LIKE_VALUES
        if isinstance(value, (list, tuple, set, dict)):
            return bool(value)
        return True

    def _infer_answers_by_rules(self, domain: str, query: str, scenario: str | None) -> dict[str, Any]:
        if domain == "pet_food":
            return self._infer_pet_food(query, scenario)
        if domain == "supplement":
            return self._infer_supplement(query, scenario)
        return {}

    def _infer_answers_by_llm(
        self,
        domain: str,
        query: str,
        scenario: str | None,
        rule_answers: dict[str, Any],
    ) -> dict[str, Any]:
        if (
            not self.llm_adapter
            or not self.llm_adapter.enabled
            or not self.llm_adapter.settings.llm_query_understanding_enabled
            or not self.llm_adapter.sdk_available()
        ):
            return {}

        result = self.llm_adapter.generate_json(
            schema_model=AIQueryUnderstanding,
            system_prompt=(
                "你是消费决策系统里的 Query Understanding Agent。"
                "只抽取用户已经明确表达的信息，不要猜测。"
                "输出字段必须能映射到已有问题 id，例如 pet_age_stage、priority、"
                "budget_range、digestive_issue、picky_eater、response_tone、"
                "health_goal、medical_context、allergy。"
                "不要输出医疗诊断。"
            ),
            user_payload={
                "domain": domain,
                "scenario": scenario,
                "query": query,
                "rule_answers": rule_answers,
            },
        )
        if not isinstance(result, AIQueryUnderstanding):
            return {}
        return result.inferred_answers

    def _infer_pet_food(self, query: str, scenario: str | None) -> dict[str, Any]:
        answers: dict[str, Any] = {}

        if any(token in query for token in ["幼猫", "奶猫", "奶糕", "断奶"]):
            answers["pet_age_stage"] = "幼猫"
        elif "老年" in query:
            answers["pet_age_stage"] = "老年猫"
        elif "成猫" in query or "成年猫" in query:
            answers["pet_age_stage"] = "成猫"

        if any(token in query for token in ["软便", "玻璃胃", "呕吐", "肠胃", "便臭", "消化"]):
            answers["digestive_issue"] = "经常"

        if any(token in query for token in ["挑食", "不爱吃", "适口性", "嘴刁", "嘴很刁", "闻一闻就走", "香一点"]):
            answers["picky_eater"] = "很挑食"

        if any(token in query for token in ["鱼肉过敏", "鱼类过敏", "三文鱼过敏", "鱼油过敏", "怀疑鱼肉", "怀疑鱼类"]):
            answers["allergy"] = "有"
            answers["avoid_ingredients"] = ["鱼肉", "鱼油", "三文鱼"]
        elif any(token in query for token in ["可能过敏", "疑似过敏", "低敏", "排查过敏"]):
            answers["allergy"] = "不确定"

        if any(token in query for token in ["不考虑性价比", "不看性价比", "高品质", "品质配方"]):
            answers["priority"] = "成分更好"
            answers["budget_range"] = "预算不敏感"
        elif any(token in query for token in ["性价比", "质价比", "便宜", "大包装", "多猫"]):
            answers["priority"] = "性价比"
            answers["budget_range"] = "预算优先"
        elif any(token in query for token in ["美毛", "泪痕", "去泪痕", "黑下巴", "毛发"]):
            answers["priority"] = "美毛泪痕"
        elif any(token in query for token in ["肠胃稳定", "护肠胃", "养胃"]):
            answers["priority"] = "肠胃稳定"
        elif any(token in query for token in ["爱吃", "冻干", "嘴刁", "嘴很刁", "闻一闻就走", "香一点"]):
            answers["priority"] = "猫爱吃"

        if scenario == "coat_tear_stain":
            answers.setdefault("priority", "美毛泪痕")
        if scenario == "kitten_growth":
            answers.setdefault("pet_age_stage", "幼猫")
            answers.setdefault("priority", "成分更好")

        return answers

    def _infer_supplement(self, query: str, scenario: str | None) -> dict[str, Any]:
        answers: dict[str, Any] = {}
        if scenario == "probiotics" or "益生菌" in query or "益生元" in query:
            answers["health_goal"] = "肠胃调理"
        elif scenario == "fish_oil" or "鱼油" in query:
            answers["health_goal"] = "心血管健康"
        elif scenario == "lutein" or "叶黄素" in query:
            answers["health_goal"] = "眼部疲劳"
        elif scenario in ["multivitamin", "calcium_vitamin_d"]:
            answers["health_goal"] = "基础营养补充"

        if any(token in query for token in ["正在服药", "吃药", "慢性病", "孕期", "儿童"]):
            answers["medical_context"] = "有"
        if any(token in query for token in ["不确定有没有", "不确定是否", "可能过敏"]):
            answers["allergy"] = "不确定"
        elif "过敏" in query:
            answers["allergy"] = "有"
        if any(token in query for token in ["预算少", "预算低", "便宜", "不贵", "省钱", "每日成本低"]):
            answers["budget_range"] = "预算优先"
        elif any(token in query for token in ["预算多", "预算高", "预算不限", "预算不敏感", "贵一点"]):
            answers["budget_range"] = "预算不敏感"
        return answers
