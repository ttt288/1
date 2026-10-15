import json
import math
import re
from hashlib import blake2b
from pathlib import Path

from app.schemas import KnowledgeEvidence, ProductOffer, UserProfile
from app.services.cache_service import TTLCache


EMBEDDING_DIMENSIONS = 256


class KnowledgeService:
    def __init__(self, data_dir: Path) -> None:
        self.knowledge_dir = data_dir / "knowledge"
        self.cache: TTLCache[list[KnowledgeEvidence]] = TTLCache(ttl_seconds=900)
        self.last_cache_hit = False
        self._document_embedding_cache: dict[str, dict[int, float]] = {}

    def retrieve(
        self,
        *,
        domain: str,
        query: str,
        products: list[ProductOffer],
        user_profile: UserProfile,
        limit: int = 4,
    ) -> list[KnowledgeEvidence]:
        cache_key = self._cache_key(domain, query, products, user_profile, limit)
        cached = self.cache.get(cache_key)
        if cached is not None:
            self.last_cache_hit = True
            return cached

        self.last_cache_hit = False
        documents = self._load_documents(domain)
        terms = self._query_terms(domain, query, products, user_profile)
        decision_terms = self._decision_terms(user_profile)
        query_text = " ".join(terms)
        query_embedding = self._embed(query_text)
        scored: list[dict] = []
        for document in documents:
            lexical_score, matched_terms, lexical_stage = self._first_stage_score(document, terms, decision_terms)
            embedding_score = self._embedding_score(document, query_embedding)
            score = self._hybrid_score(lexical_score, embedding_score)
            if score <= 0.18:
                continue
            scored.append(
                {
                    "document": document,
                    "score": score,
                    "lexical_score": lexical_score,
                    "embedding_score": embedding_score,
                    "matched_terms": matched_terms,
                    "stage": self._retrieval_stage(lexical_stage, embedding_score),
                }
            )

        reranked = self._rerank(scored, decision_terms, limit)
        evidence = [
            KnowledgeEvidence(
                id=f"evidence_{index + 1}",
                domain=item["document"]["domain"],
                source=item["document"]["source"],
                title=item["document"]["title"],
                content=item["document"]["content"],
                matched_terms=item["matched_terms"][:8],
                relevance_score=round(item["score"], 2),
                chunk_id=item["document"].get("chunk_id"),
                chunk_type=item["document"].get("chunk_type", "rule"),
                decision_dimensions=item["document"].get("decision_dimensions", []),
                retrieval_stage=item["stage"],
                rerank_score=round(item["rerank_score"], 2),
                rerank_reason=item["rerank_reason"],
                embedding_score=round(item.get("embedding_score", 0), 2),
            )
            for index, item in enumerate(reranked)
        ]
        self.cache.set(cache_key, evidence)
        return evidence

    def _load_documents(self, domain: str) -> list[dict]:
        paths = [self.knowledge_dir / "ingredient_glossary.md"]
        if domain == "pet_food":
            paths.append(self.knowledge_dir / "pet_food_rules.md")
        elif domain == "supplement":
            paths.append(self.knowledge_dir / "supplement_rules.md")

        documents = []
        structured_path = self.knowledge_dir / "decision_chunks.json"
        if structured_path.exists():
            chunks = json.loads(structured_path.read_text(encoding="utf-8"))
            documents.extend(
                {
                    "domain": chunk["domain"],
                    "source": chunk.get("source", structured_path.name),
                    "title": chunk["title"],
                    "content": chunk["content"],
                    "line": 0,
                    "chunk_id": chunk["chunk_id"],
                    "chunk_type": chunk.get("chunk_type", "rule"),
                    "decision_dimensions": chunk.get("decision_dimensions", []),
                    "keywords": chunk.get("keywords", []),
                }
                for chunk in chunks
                if chunk.get("domain") == domain
            )
        for path in paths:
            if not path.exists():
                continue
            title = ""
            for index, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("#"):
                    title = line.lstrip("#").strip()
                    continue
                content = re.sub(r"^-+\s*", "", line)
                documents.append(
                    {
                        "domain": domain,
                        "source": path.name,
                        "title": title or path.stem,
                        "content": content,
                        "line": index + 1,
                        "chunk_id": f"{path.stem}_{index + 1}",
                        "chunk_type": "legacy_markdown",
                        "decision_dimensions": [],
                        "keywords": [],
                    }
                )
        return documents

    def _first_stage_score(
        self,
        document: dict,
        terms: list[str],
        decision_terms: set[str],
    ) -> tuple[float, list[str], str]:
        content = f"{document['title']} {document['content']}"
        keywords = document.get("keywords", [])
        dimensions = set(document.get("decision_dimensions", []))
        matched_query_terms = [term for term in terms if term and term in content]
        matched_keywords = [term for term in keywords if term and term in " ".join(terms)]
        matched_dimensions = sorted(dimensions & decision_terms)
        if not matched_query_terms and not matched_keywords and not matched_dimensions:
            return 0, [], "none"

        lexical_score = len(set(matched_query_terms)) / max(len(set(terms)), 1)
        metadata_score = len(set(matched_keywords)) / max(len(set(keywords)), 1) if keywords else 0
        dimension_score = len(matched_dimensions) / max(len(dimensions), 1) if dimensions else 0
        score = min(1.0, lexical_score * 0.45 + metadata_score * 0.35 + dimension_score * 0.20 + 0.18)
        stage = "hybrid_keyword_metadata"
        matched_terms = list(dict.fromkeys(matched_query_terms + matched_keywords + matched_dimensions))
        return score, matched_terms, stage

    def _rerank(self, scored: list[dict], decision_terms: set[str], limit: int) -> list[dict]:
        for item in scored:
            document = item["document"]
            dimensions = set(document.get("decision_dimensions", []))
            type_boost = {
                "guardrail": 0.12,
                "risk_rule": 0.10,
                "domain_rule": 0.08,
                "pricing_rule": 0.06,
                "decision_rule": 0.06,
            }.get(document.get("chunk_type", "rule"), 0.02)
            dimension_overlap = len(dimensions & decision_terms)
            semantic_boost = item.get("embedding_score", 0) * 0.14
            item["rerank_score"] = min(
                1.0,
                item["score"] + type_boost + dimension_overlap * 0.07 + semantic_boost,
            )
            item["rerank_reason"] = self._rerank_reason(document, dimension_overlap)

        pool = sorted(scored, key=lambda item: (item["rerank_score"], len(item["matched_terms"])), reverse=True)
        selected: list[dict] = []
        used_types: set[str] = set()
        used_dimensions: set[str] = set()
        for item in pool:
            chunk_type = item["document"].get("chunk_type", "rule")
            dimensions = set(item["document"].get("decision_dimensions", []))
            diversity_penalty = 0
            if chunk_type in used_types:
                diversity_penalty += 0.04
            if dimensions and dimensions <= used_dimensions:
                diversity_penalty += 0.06
            item["rerank_score"] = max(0, item["rerank_score"] - diversity_penalty)
            selected.append(item)
            used_types.add(chunk_type)
            used_dimensions |= dimensions
            if len(selected) >= limit:
                break

        selected.sort(key=lambda item: item["rerank_score"], reverse=True)
        return selected

    def _hybrid_score(self, lexical_score: float, embedding_score: float) -> float:
        if lexical_score <= 0 and embedding_score < 0.16:
            return 0
        return min(1.0, lexical_score * 0.52 + embedding_score * 0.48)

    def _retrieval_stage(self, lexical_stage: str, embedding_score: float) -> str:
        if lexical_stage != "none" and embedding_score >= 0.16:
            return "embedding_keyword_hybrid"
        if embedding_score >= 0.16:
            return "embedding_vector"
        return lexical_stage

    def _embedding_score(self, document: dict, query_embedding: dict[int, float]) -> float:
        cache_key = str(document.get("chunk_id") or f"{document.get('source')}:{document.get('line')}")
        document_embedding = self._document_embedding_cache.get(cache_key)
        if document_embedding is None:
            text = self._document_text(document)
            document_embedding = self._embed(text)
            self._document_embedding_cache[cache_key] = document_embedding
        return self._cosine(query_embedding, document_embedding)

    def _document_text(self, document: dict) -> str:
        return " ".join(
            [
                str(document.get("title", "")),
                str(document.get("content", "")),
                " ".join(document.get("keywords", [])),
                " ".join(document.get("decision_dimensions", [])),
                str(document.get("chunk_type", "")),
            ]
        )

    def _embed(self, text: str) -> dict[int, float]:
        tokens = self._embedding_tokens(text)
        if not tokens:
            return {}
        vector: dict[int, float] = {}
        for token in tokens:
            digest = blake2b(token.encode("utf-8"), digest_size=4).digest()
            index = int.from_bytes(digest, "big") % EMBEDDING_DIMENSIONS
            vector[index] = vector.get(index, 0.0) + 1.0
        norm = math.sqrt(sum(value * value for value in vector.values())) or 1.0
        return {index: value / norm for index, value in vector.items()}

    def _embedding_tokens(self, text: str) -> list[str]:
        normalized = re.sub(r"\s+", "", text.lower())
        cjk_tokens = [
            normalized[index : index + size]
            for size in (2, 3)
            for index in range(max(len(normalized) - size + 1, 0))
        ]
        word_tokens = re.findall(r"[a-z0-9_]{2,}", text.lower())
        return cjk_tokens + word_tokens

    def _cosine(self, left: dict[int, float], right: dict[int, float]) -> float:
        if not left or not right:
            return 0
        if len(left) > len(right):
            left, right = right, left
        score = sum(value * right.get(index, 0.0) for index, value in left.items())
        return max(0.0, min(1.0, score))

    def _rerank_reason(self, document: dict, dimension_overlap: int) -> str:
        chunk_type = document.get("chunk_type", "rule")
        dimensions = "、".join(document.get("decision_dimensions", [])[:3]) or "通用规则"
        if dimension_overlap:
            return f"命中当前决策维度：{dimensions}"
        if chunk_type in ["guardrail", "risk_rule"]:
            return "作为风险边界证据保留"
        if chunk_type == "pricing_rule":
            return "用于解释预算和参考单价"
        return "用于补充推荐解释"

    def _query_terms(
        self,
        domain: str,
        query: str,
        products: list[ProductOffer],
        user_profile: UserProfile,
    ) -> list[str]:
        answers = user_profile.scenario_profile
        terms = [
            query,
            str(answers.get("priority", "")),
            str(answers.get("health_goal", "")),
            str(answers.get("digestive_issue", "")),
            str(answers.get("medical_context", "")),
            str(answers.get("allergy", "")),
            str(answers.get("pet_age_stage", "")),
        ]
        if domain == "pet_food":
            if answers.get("digestive_issue") in ["经常", "偶尔"]:
                terms.extend(["肠胃敏感", "软便", "呕吐", "换粮"])
            if answers.get("pet_age_stage"):
                terms.extend(["幼猫", "成猫", "老年猫", "蛋白", "脂肪"])
            if answers.get("priority") == "美毛泪痕":
                terms.extend(["鱼油", "泪痕", "美毛", "过敏"])
        elif domain == "supplement":
            terms.extend(["保健品", "诊断", "治疗", "过敏", "服药", "慢性病"])

        for product in products[:3]:
            terms.extend(product.risk_tags)
            terms.extend(str(value) for value in product.features.values() if isinstance(value, str))
            terms.extend(product.strengths)
            terms.extend(product.target_users)

        expanded = []
        for term in terms:
            if not term or term == "None":
                continue
            expanded.append(term)
            expanded.extend(self._split_terms(str(term)))
        return list(dict.fromkeys(item for item in expanded if item))

    def _split_terms(self, value: str) -> list[str]:
        return [
            token
            for token in re.split(r"[\s,，、/|;；:：()（）\[\]{}]+", value)
            if len(token) >= 2
        ]

    def _decision_terms(self, user_profile: UserProfile) -> set[str]:
        answers = user_profile.scenario_profile
        terms = {key for key, value in answers.items() if value not in [None, ""]}
        if answers.get("priority"):
            terms.add("priority")
        if answers.get("budget_range"):
            terms.update(["budget_range", "unit_price", "value"])
        if answers.get("pet_age_stage"):
            terms.update(["pet_age_stage", "life_stage"])
        if answers.get("digestive_issue"):
            terms.update(["digestive_issue", "risk", "transition"])
        if answers.get("picky_eater"):
            terms.update(["picky_eater", "palatability"])
        if answers.get("medical_context"):
            terms.update(["medical_context", "risk"])
        if answers.get("allergy"):
            terms.update(["allergy", "risk"])
        return terms

    def _cache_key(
        self,
        domain: str,
        query: str,
        products: list[ProductOffer],
        user_profile: UserProfile,
        limit: int,
    ) -> str:
        product_ids = ",".join(product.id for product in products)
        profile_items = sorted(
            (key, str(value))
            for key, value in user_profile.scenario_profile.items()
            if not key.startswith("_")
        )
        return f"{domain}:{query.strip()}:{product_ids}:{profile_items}:{limit}"
