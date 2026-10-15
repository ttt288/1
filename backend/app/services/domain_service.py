import json
from pathlib import Path

from app.schemas import DomainRoute


class DomainService:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self._domains = self._load_domains()

    def _load_domains(self) -> list[dict]:
        domains_dir = self.data_dir / "domains"
        return [
            json.loads(path.read_text(encoding="utf-8"))
            for path in sorted(domains_dir.glob("*.json"))
        ]

    def route(self, query: str) -> DomainRoute:
        query_lower = query.lower()
        best_domain = None
        best_score = 0

        for domain in self._domains:
            if domain["domain"] == "general":
                continue
            score = 0
            for keyword in domain["query_keywords"]:
                if keyword.lower() in query_lower:
                    score += 2
            for scenario in domain.get("sub_scenarios", []):
                for keyword in scenario.get("keywords", []):
                    if keyword.lower() in query_lower and not self._is_negated(query_lower, keyword.lower()):
                        score += 1
            if score > best_score:
                best_domain = domain
                best_score = score

        if best_domain is None:
            best_domain = self._fallback_domain()
            scenario, scenario_score, evidence = self._match_scenario(best_domain, query)
            return DomainRoute(
                domain=best_domain["domain"],
                intent="unknown_consumer_decision",
                confidence=0.35,
                need_clarification=True,
                scenario=scenario,
                scenario_confidence=scenario_score,
                scenario_evidence=evidence,
            )

        confidence = min(0.95, 0.55 + best_score * 0.15)
        scenario, scenario_score, evidence = self._match_scenario(best_domain, query)
        return DomainRoute(
            domain=best_domain["domain"],
            intent=f"{best_domain['domain']}_recommendation",
            confidence=confidence,
            need_clarification=confidence < 0.6,
            scenario=scenario,
            scenario_confidence=scenario_score,
            scenario_evidence=evidence,
        )

    def _fallback_domain(self) -> dict:
        for domain in self._domains:
            if domain["domain"] == "general":
                return domain
        return self._domains[0]

    def _match_scenario(self, domain: dict, query: str) -> tuple[str | None, float, list[str]]:
        scenarios = domain.get("sub_scenarios", [])
        query_lower = query.lower()
        best: dict | None = None
        best_matches: list[str] = []
        best_weight = 0.0

        for scenario in scenarios:
            matches = [
                keyword
                for keyword in scenario.get("keywords", [])
                if keyword.lower() in query_lower and not self._is_negated(query_lower, keyword.lower())
            ]
            weight = sum(self._scenario_keyword_weight(keyword) for keyword in matches)
            if weight > best_weight:
                best = scenario
                best_matches = matches
                best_weight = weight

        if best is None:
            return None, 0, []

        confidence = min(0.95, 0.45 + best_weight * 0.12)
        return best["id"], confidence, best_matches

    def _scenario_keyword_weight(self, keyword: str) -> float:
        if keyword in ["成猫", "成年猫", "日常", "普通", "主粮", "猫"]:
            return 1.0
        if keyword in ["幼猫", "奶猫", "奶糕", "软便", "呕吐", "肠胃", "玻璃胃", "美毛", "毛发", "泪痕", "挑食", "不爱吃", "嘴刁"]:
            return 2.0
        return 1.5

    def _is_negated(self, query: str, keyword: str) -> bool:
        index = query.find(keyword)
        if index < 0:
            return False
        window = query[max(0, index - 8):index]
        return any(token in window for token in ["别", "不要", "不想", "排除", "避开"])

    def get_config(self, domain: str) -> dict:
        for item in self._domains:
            if item["domain"] == domain:
                return item
        raise ValueError(f"Unsupported domain: {domain}")
