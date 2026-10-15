import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from app.agents import LLMAdapter
from app.schemas import ProductOffer
from app.schemas.ai import AIHotlistDiscovery, AIProductDiscovery
from app.services.jd_ranking_service import JDRankingService


@dataclass(frozen=True)
class HotlistSource:
    id: str
    name: str
    domain: str
    scenario: str | None
    keywords: list[str]
    url: str
    heading_keywords: list[str]
    search_keyword: str


class ExternalDiscoveryService:
    """Select known external hotlist sources, then fall back to a search entry."""

    def __init__(self, data_dir: Path, llm_adapter: LLMAdapter | None = None) -> None:
        self.data_dir = data_dir
        self.llm_adapter = llm_adapter
        self.last_llm_input_tokens = 0
        self.last_llm_output_tokens = 0
        self.last_discovery_method = "registry"
        self.sources = self._load_sources()

    def _load_sources(self) -> list[HotlistSource]:
        sources: list[HotlistSource] = []
        path = self.data_dir / "market_sources" / "jd_hotlists.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            sources.extend(HotlistSource(**item) for item in raw)
        dynamic_path = self.data_dir / "market_sources" / "jd_dynamic_sources.json"
        if dynamic_path.exists():
            raw = json.loads(dynamic_path.read_text(encoding="utf-8"))
            sources.extend(HotlistSource(**item) for item in raw)
        sources.extend(self._load_snapshot_sources({source.id for source in sources}))
        return sources

    def _load_snapshot_sources(self, known_ids: set[str]) -> list[HotlistSource]:
        directory = self.data_dir / "market_sources"
        if not directory.exists():
            return []
        sources = []
        for snapshot_path in directory.glob("jd_dynamic_*/current.json"):
            source_id = snapshot_path.parent.name
            if source_id in known_ids:
                continue
            try:
                snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            keyword = self._keyword_from_source_name(snapshot.get("source_name") or "商品")
            sources.append(HotlistSource(
                id=source_id,
                name=snapshot.get("source_name") or f"京东{keyword}公开排行榜",
                domain="general",
                scenario="general_consumer_decision",
                keywords=[keyword],
                url=snapshot.get("source_url") or "",
                heading_keywords=["京东", "排行榜"],
                search_keyword=keyword,
            ))
        return sources

    def discover(self, query: str, domain: str, scenario: str | None, refresh: bool = False) -> dict:
        source = self.select_source(query=query, domain=domain, scenario=scenario)
        discovered_source = None
        discovery_error = None
        self.last_llm_input_tokens = 0
        self.last_llm_output_tokens = 0
        self.last_discovery_method = "registry" if source else "none"
        if not source and refresh:
            try:
                discovered_source = self.discover_hotlist_source_by_llm(query=query, domain=domain, scenario=scenario)
                self.last_discovery_method = "llm_web_search"
                self._remember_dynamic_source(discovered_source)
                source = discovered_source
            except (ValueError, OSError) as exc:
                discovery_error = str(exc)[:240]
            try:
                if not source:
                    discovered_source = self.discover_hotlist_source(query=query, domain=domain, scenario=scenario)
                    self.last_discovery_method = "search_engine"
                    self._remember_dynamic_source(discovered_source)
                source = discovered_source
            except (httpx.HTTPError, ValueError, OSError) as exc:
                discovery_error = "; ".join(part for part in [discovery_error, str(exc)[:240]] if part)
        if not source:
            note = "没有注册可校验的公开热榜源"
            if discovery_error:
                note += f"，自动搜索也未成功：{discovery_error}"
            return self._search_fallback(query, domain, scenario, None, note)

        service = JDRankingService(
            self.data_dir,
            source_key=source.id,
            source_url=source.url,
            source_name=source.name,
            expected_heading_keywords=source.heading_keywords,
        )
        result = service.refresh(force=True) if refresh else service.status()
        snapshot = result.get("snapshot")
        return {
            "mode": "hotlist_snapshot" if snapshot else "search_fallback",
            "domain": domain,
            "scenario": scenario,
            "selected_source": self._source_payload(source),
            "source_discovered_online": discovered_source is not None,
            "discovery_method": self.last_discovery_method,
            "snapshot": snapshot,
            "status": result.get("status"),
            "refresh_result": result.get("refresh_result"),
            "error": result.get("error"),
            "stale": result.get("stale"),
            "search_url": self._jd_search_url(source.search_keyword or query),
            "source_note": (
                "已选中公开热榜源；快照条目只作为候选召回和热度参考，价格、规格和参数仍需核验。"
                if snapshot
                else "已选中公开热榜源，但暂未取得可解析快照；先回退到京东搜索入口。"
            ),
        }

    def discover_hotlist_source_by_llm(self, query: str, domain: str, scenario: str | None) -> HotlistSource:
        if (
            not self.llm_adapter
            or not self.llm_adapter.enabled
            or not self.llm_adapter.settings.llm_web_search_enabled
            or not self.llm_adapter.sdk_available()
        ):
            raise ValueError("LLM web search disabled or unavailable")
        keyword = self._category_keyword(query)
        result = self.llm_adapter.generate_json(
            schema_model=AIHotlistDiscovery,
            system_prompt=(
                "你是 Hotlist Search Agent。任务是为中国大陆消费品推荐系统寻找公开可访问的京东排行榜页面。"
                "优先返回 www.jd.com/phb/key_*.html 这种京东排行榜 URL。"
                "不要返回商品详情页、搜索页、广告页或需要登录的页面。"
                "如果找不到可信榜单，hotlist_urls 返回空数组。"
            ),
            user_payload={
                "query": query,
                "category_keyword": keyword,
                "preferred_domain": "jd.com/phb",
                "domain": domain,
                "scenario": scenario,
            },
            tools=[{"type": "web_search_preview", "search_context_size": "low"}],
        )
        self.last_llm_input_tokens = self.llm_adapter.last_input_tokens
        self.last_llm_output_tokens = self.llm_adapter.last_output_tokens
        if not isinstance(result, AIHotlistDiscovery):
            raise ValueError(self.llm_adapter.last_error or "LLM web search did not return valid JSON")
        urls = [
            url for url in result.hotlist_urls
            if re.fullmatch(r"https?://www\.jd\.com/phb/key_[A-Za-z0-9]+\.html", url)
        ]
        if not urls:
            raise ValueError("LLM web search did not find jd.com/phb hotlist URL")
        source_url = urls[0]
        source_key = "jd_dynamic_" + re.sub(r"[^a-zA-Z0-9]+", "_", urlparse(source_url).path).strip("_")[-48:]
        return HotlistSource(
            id=source_key,
            name=f"京东{result.category_keyword or keyword}公开排行榜",
            domain=domain,
            scenario=scenario,
            keywords=[result.category_keyword or keyword],
            url=source_url,
            heading_keywords=["京东", "排行榜"],
            search_keyword=result.category_keyword or keyword,
        )

    def discover_hotlist_source(self, query: str, domain: str, scenario: str | None) -> HotlistSource:
        keyword = self._category_keyword(query)
        urls = []
        errors = []
        search_queries = [
            ("sogou", "https://www.sogou.com/web?query=" + quote(f"site:jd.com/phb 京东 {keyword} 排行榜")),
            ("bing", "https://www.bing.com/search?q=" + quote(f"site:jd.com/phb 京东 {keyword} 排行榜")),
        ]
        with httpx.Client(timeout=httpx.Timeout(8, connect=3), follow_redirects=True) as client:
            for engine, search_url in search_queries:
                try:
                    response = client.get(
                        search_url,
                        headers={
                            "User-Agent": "Mozilla/5.0 ShoppingDecisionResearch/1.0",
                            "Accept": "text/html",
                        },
                    )
                    response.raise_for_status()
                    urls = self._extract_jd_phb_urls(response.text)
                    if urls:
                        break
                    errors.append(f"{engine}: 未找到 jd.com/phb 链接")
                except httpx.HTTPError as exc:
                    errors.append(f"{engine}: {type(exc).__name__}")
        if not urls:
            raise ValueError("没有在搜索结果中找到 jd.com/phb 榜单 URL；" + "；".join(errors))
        source_url = urls[0]
        source_key = "jd_dynamic_" + re.sub(r"[^a-zA-Z0-9]+", "_", urlparse(source_url).path).strip("_")[-48:]
        return HotlistSource(
            id=source_key,
            name=f"京东{keyword}公开排行榜",
            domain=domain,
            scenario=scenario,
            keywords=[keyword],
            url=source_url,
            heading_keywords=["京东", "排行榜"],
            search_keyword=keyword,
        )

    def _extract_jd_phb_urls(self, html: str) -> list[str]:
        urls = re.findall(r"https?://www\.jd\.com/phb/key_[A-Za-z0-9]+\.html", html)
        hrefs = re.findall(r"href=[\"']([^\"']+)[\"']", html)
        for href in hrefs:
            text = unquote(href)
            urls.extend(re.findall(r"https?://www\.jd\.com/phb/key_[A-Za-z0-9]+\.html", text))
        return list(dict.fromkeys(urls))

    def select_source(self, query: str, domain: str, scenario: str | None) -> HotlistSource | None:
        text = f"{query} {scenario or ''}".lower()
        best: tuple[int, HotlistSource] | None = None
        for source in self.sources:
            score = 0
            if source.domain == domain:
                score += 5
            if source.scenario and source.scenario == scenario:
                score += 4
            keyword_hits = sum(1 for keyword in source.keywords if keyword.lower() in text)
            scenario_hit = bool(source.scenario and source.scenario == scenario and scenario != "general_consumer_decision")
            domain_default = source.id == "jd_pet_food" and domain == "pet_food"
            if not keyword_hits and not scenario_hit and not domain_default:
                continue
            score += keyword_hits * 3
            if score > 0 and (best is None or score > best[0]):
                best = (score, source)
        return best[1] if best else None

    def to_product_offers(self, discovery: dict, domain: str, limit: int = 20) -> list[ProductOffer]:
        snapshot = discovery.get("snapshot") or {}
        entries = snapshot.get("entries") or []
        source = discovery.get("selected_source") or {}
        products: list[ProductOffer] = []
        for entry in entries[:limit]:
            rank = int(entry["rank"])
            price = float(entry["price"] or 0)
            observed_at = snapshot.get("observed_at") or ""
            claim_terms = self._claim_terms(entry["title"])
            products.append(ProductOffer(
                id=f"external_{source.get('id', 'jd')}_{entry['jd_sku']}",
                domain=domain,
                name=entry["title"],
                brand=self._brand_from_title(entry["title"]),
                price=price,
                spec=entry.get("spec") or "规格待核验",
                unit_price=None,
                popularity_score=max(40, 101 - rank),
                features={
                    "external_hotlist": True,
                    "price_unverified": True,
                    "scenario": discovery.get("scenario"),
                    "detected_claims": claim_terms,
                },
                strengths=(claim_terms[:2] or ["公开热榜候选"]) + ["外部热度较高"],
                weaknesses=["参数、规格和价格未结构化核验", "只能作为候选召回，不能替代最终核验"],
                risk_tags=["external_data_unverified"],
                target_users=["需要先看热门候选的人群"],
                avoid_for=[],
                source_type="external_hotlist",
                source_url=entry.get("product_url"),
                source_name=snapshot.get("source_name") or source.get("name"),
                market_rank=rank,
                review_count_label=entry.get("review_count_label"),
                data_quality="external_hotlist_price_unverified",
                jd_sku=entry.get("jd_sku"),
                ranking_observed_at=observed_at,
                last_updated=observed_at[:10] or "待核验",
            ))
        return products

    def discover_product_candidates(self, query: str, discovery: dict, limit: int = 12) -> list[ProductOffer]:
        keyword = self._category_keyword(query)
        search_url = discovery.get("search_url") or self._jd_search_url(keyword)
        candidates = self._discover_jd_search_products(search_url, limit=limit)
        source_type = "jd_search_result"
        if not candidates:
            candidates = self._discover_products_by_llm(query=query, keyword=keyword, limit=limit)
            source_type = "llm_web_search_jd_candidate"
        return [
            self._candidate_to_product(candidate, index, source_type)
            for index, candidate in enumerate(candidates[:limit], start=1)
        ]

    def _discover_jd_search_products(self, search_url: str, limit: int = 12) -> list[dict]:
        try:
            with httpx.Client(timeout=httpx.Timeout(10, connect=4), follow_redirects=True) as client:
                response = client.get(
                    search_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 ShoppingDecisionResearch/1.0",
                        "Accept": "text/html",
                    },
                )
                response.raise_for_status()
        except httpx.HTTPError:
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[dict] = []
        for item in soup.select("li.gl-item"):
            sku = item.get("data-sku") or ""
            link = item.select_one(".p-name a[href*='item.jd.com'], .p-img a[href*='item.jd.com']")
            name_node = item.select_one(".p-name em") or item.select_one(".p-name a")
            if not link or not name_node:
                continue
            product_url = self._absolute_jd_item_url(link.get("href", ""))
            title = name_node.get_text(" ", strip=True)
            if not product_url or not title:
                continue
            price_node = item.select_one(".p-price i")
            price_text = price_node.get_text(strip=True) if price_node else ""
            candidates.append({
                "name": self._clean_title(title),
                "brand": self._brand_from_title(title),
                "product_url": product_url,
                "jd_sku": sku or self._sku_from_url(product_url),
                "price": float(price_text) if re.fullmatch(r"\d+(?:\.\d{1,2})?", price_text) else 0,
                "claims": self._claim_terms(title),
                "reason": "来自京东搜索结果，价格和参数需打开商品页核验。",
            })
            if len(candidates) >= limit:
                break
        return candidates

    def _discover_products_by_llm(self, query: str, keyword: str, limit: int = 12) -> list[dict]:
        if (
            not self.llm_adapter
            or not self.llm_adapter.enabled
            or not self.llm_adapter.settings.llm_web_search_enabled
            or not self.llm_adapter.sdk_available()
        ):
            return []
        result = self.llm_adapter.generate_json(
            schema_model=AIProductDiscovery,
            system_prompt=(
                "你是 JD Product Search Agent。请用 web search 查找中国大陆京东可打开的真实商品候选。"
                "必须返回品牌+具体型号/款名，不要返回类型、泛化方案或营销分类。"
                "product_url 优先是 https://item.jd.com/{sku}.html；无法确认 URL 时留空但 name 必须是具体商品名。"
                "不要编造价格。claims 只填写搜索结果标题中能看到的卖点。"
            ),
            user_payload={
                "query": query,
                "category_keyword": keyword,
                "preferred_site": "item.jd.com",
                "limit": limit,
            },
            tools=[{"type": "web_search_preview", "search_context_size": "low"}],
            max_output_tokens=900,
        )
        self.last_llm_input_tokens = self.llm_adapter.last_input_tokens
        self.last_llm_output_tokens = self.llm_adapter.last_output_tokens
        if not isinstance(result, AIProductDiscovery):
            return []
        candidates = []
        for item in result.products[:limit]:
            url = item.product_url if re.match(r"https://item\.jd\.com/\d{5,20}\.html", item.product_url) else ""
            candidates.append({
                "name": item.name,
                "brand": item.brand or self._brand_from_title(item.name),
                "product_url": url,
                "jd_sku": self._sku_from_url(url),
                "price": 0,
                "claims": item.claims or self._claim_terms(item.name),
                "reason": item.reason or "来自 LLM web search 的京东候选，价格和参数需人工核验。",
            })
        return candidates

    def _candidate_to_product(self, candidate: dict, index: int, source_type: str) -> ProductOffer:
        title = candidate["name"]
        claims = candidate.get("claims") or self._claim_terms(title)
        return ProductOffer(
            id=f"{source_type}_{candidate.get('jd_sku') or self._slug(title)}_{index}",
            domain="general",
            name=title,
            brand=candidate.get("brand") or self._brand_from_title(title),
            price=float(candidate.get("price") or 0),
            spec="规格待核验",
            unit_price=None,
            popularity_score=max(45, 98 - index * 3),
            features={
                "external_search_result": True,
                "price_unverified": True,
                "detected_claims": claims,
            },
            strengths=(claims[:2] or ["真实商品候选"]) + ["可跳转京东核验"],
            weaknesses=["价格、规格和参数需打开商品页核验", "搜索结果不等于最终销量排名"],
            risk_tags=["external_data_unverified"],
            target_users=["需要真实商品候选的人群", *claims[:3]],
            avoid_for=[],
            source_type=source_type,
            source_url=candidate.get("product_url") or self._jd_search_url(title),
            source_name="京东搜索/LLM web search",
            market_rank=index,
            review_count_label=None,
            data_quality="jd_candidate_price_unverified",
            jd_sku=candidate.get("jd_sku") or None,
            ranking_observed_at=None,
            last_updated="待核验",
        )

    def to_search_fallback_offers(self, discovery: dict, query: str, limit: int = 12) -> list[ProductOffer]:
        """Build verifiable-by-user candidate archetypes when no hotlist snapshot exists.

        These are not real-time products or prices. They keep the decision workflow useful
        for arbitrary categories while making the data boundary explicit.
        """
        category = self._category_from_query(query)
        archetypes = self._general_archetypes(category)
        search_url = discovery.get("search_url") or self._jd_search_url(category)
        products: list[ProductOffer] = []
        for index, item in enumerate(archetypes[:limit], start=1):
            claims = item["claims"]
            products.append(ProductOffer(
                id=f"general_search_{self._slug(category)}_{index}",
                domain="general",
                name=f"{category}{item['name']}",
                brand="京东搜索候选方案",
                price=0,
                spec="价格与 SKU 待核验",
                unit_price=None,
                popularity_score=max(50, 92 - index * 3),
                features={
                    "category": category,
                    "external_search_fallback": True,
                    "price_unverified": True,
                    "detected_claims": claims,
                    "decision_dimension": item["dimension"],
                },
                strengths=item["strengths"],
                weaknesses=item["weaknesses"],
                risk_tags=["external_data_unverified"],
                target_users=item["target_users"],
                avoid_for=[],
                source_type="external_search_archetype",
                source_url=search_url,
                source_name="京东搜索入口",
                market_rank=index,
                review_count_label=None,
                data_quality="search_archetype_price_unverified",
                jd_sku=None,
                ranking_observed_at=None,
                last_updated="待核验",
            ))
        return products

    def _search_fallback(
        self,
        query: str,
        domain: str,
        scenario: str | None,
        source: HotlistSource | None,
        note: str,
    ) -> dict:
        keyword = source.search_keyword if source else query
        return {
            "mode": "search_fallback",
            "domain": domain,
            "scenario": scenario,
            "selected_source": self._source_payload(source) if source else None,
            "source_discovered_online": False,
            "discovery_method": self.last_discovery_method,
            "snapshot": None,
            "status": "unavailable",
            "refresh_result": None,
            "error": None,
            "stale": True,
            "search_url": self._jd_search_url(keyword),
            "source_note": note,
        }

    def _source_payload(self, source: HotlistSource) -> dict:
        return {
            "id": source.id,
            "name": source.name,
            "domain": source.domain,
            "scenario": source.scenario,
            "keywords": source.keywords,
            "url": source.url,
            "search_keyword": source.search_keyword,
        }

    def _jd_search_url(self, keyword: str) -> str:
        return f"https://search.jd.com/Search?keyword={quote(keyword)}"

    def _absolute_jd_item_url(self, href: str) -> str:
        if href.startswith("//"):
            href = "https:" + href
        parsed = urlparse(href)
        if parsed.scheme != "https" or parsed.netloc != "item.jd.com":
            return ""
        sku = self._sku_from_url(href)
        return f"https://item.jd.com/{sku}.html" if sku else ""

    def _sku_from_url(self, url: str) -> str:
        match = re.search(r"item\.jd\.com/(\d{5,20})\.html", url)
        return match.group(1) if match else ""

    def _clean_title(self, title: str) -> str:
        return re.sub(r"\s+", " ", title).replace("京东超市", "").strip()

    def _category_from_query(self, query: str) -> str:
        cleaned = re.sub(r"[，。,\.！!？?、]", " ", query)
        stop_words = ["不知道怎么选", "不知道如何选", "怎么选", "如何选", "哪个适合我", "适合我", "通勤用", "日常用", "宿舍用"]
        for word in stop_words:
            cleaned = cleaned.replace(word, " ")
        for prefix in ["我想买", "想买", "我要买", "买", "想选", "选"]:
            cleaned = cleaned.replace(prefix, " ")
        stop_words = ["不知道", "怎么", "如何"]
        for word in stop_words:
            cleaned = cleaned.replace(word, " ")
        tokens = [token.strip() for token in cleaned.split() if token.strip()]
        if not tokens:
            return "商品"
        candidate = min(tokens, key=len)
        return candidate[:12] or "商品"

    def _slug(self, text: str) -> str:
        return re.sub(r"[^a-zA-Z0-9]+", "_", text.encode("unicode_escape").decode("ascii")).strip("_")[:40] or "item"

    def _general_archetypes(self, category: str) -> list[dict]:
        if any(token in category for token in ["电脑", "笔记本", "平板"]):
            return [
                self._arch("轻薄办公本", ["便携", "续航", "办公学习"], ["轻便、续航优先", "适合通勤办公"], ["游戏和重负载性能有限"], "使用场景", ["通勤办公", "学生党"]),
                self._arch("性能全能本", ["性能", "多任务", "剪辑"], ["处理器和散热更强", "兼顾办公与轻创作"], ["重量和价格通常更高"], "性能需求", ["剪辑修图", "多任务办公"]),
                self._arch("游戏本", ["显卡", "高刷新", "散热"], ["图形性能强", "适合大型游戏"], ["厚重、续航短、噪音更明显"], "性能需求", ["游戏玩家", "3D/渲染"]),
                self._arch("高性价比入门本", ["预算优先", "基础办公", "网课"], ["预算压力小", "满足基础办公学习"], ["屏幕、做工和性能上限有限"], "预算策略", ["预算优先", "轻度使用"]),
                self._arch("高端商务本", ["做工", "屏幕", "售后"], ["做工和屏幕体验更好", "售后和稳定性更强"], ["预算要求高"], "体验偏好", ["商务办公", "预算不敏感"]),
            ]
        if any(token in category for token in ["粉底", "底妆", "气垫"]):
            return [
                self._arch("持妆控油型", ["控油", "持妆", "油皮"], ["更适合油皮和长时间带妆", "夏天或通勤更稳"], ["干皮可能卡粉"], "肤质妆效", ["油皮", "混油皮"]),
                self._arch("滋润服帖型", ["滋润", "干皮", "服帖"], ["更适合干皮和轻薄妆感", "卡粉风险较低"], ["遮瑕和控油通常弱一些"], "肤质妆效", ["干皮", "敏感干皮"]),
                self._arch("高遮瑕型", ["遮瑕", "痘印", "瑕疵"], ["遮盖力更强", "适合痘印和肤色不均"], ["厚重感和闷痘风险更高"], "遮瑕需求", ["瑕疵皮", "重要场合"]),
                self._arch("自然裸妆型", ["轻薄", "自然", "通勤"], ["妆感自然、日常不突兀", "上手门槛低"], ["遮瑕有限"], "妆效偏好", ["通勤", "新手"]),
                self._arch("敏感肌友好型", ["敏感肌", "温和", "低刺激"], ["更重视温和和稳定", "降低踩雷风险"], ["色号和妆效可能选择更少"], "风险边界", ["敏感肌", "屏障不稳"]),
            ]
        if any(token in category for token in ["耳机", "耳麦", "降噪"]):
            return [
                self._arch("主动降噪型", ["降噪", "通勤", "地铁"], ["通勤环境更安静", "适合飞机地铁"], ["价格和耳压需要关注"], "使用场景", ["通勤", "差旅"]),
                self._arch("音质优先型", ["音质", "解析", "低延迟"], ["听感和解析更好", "适合音乐党"], ["降噪和便携可能不是最强"], "核心体验", ["音乐", "影音"]),
                self._arch("运动佩戴型", ["防水", "稳固", "运动"], ["佩戴更稳", "更适合跑步健身"], ["音质上限通常一般"], "佩戴场景", ["运动", "户外"]),
                self._arch("高性价比型", ["预算优先", "入门", "耐用"], ["价格门槛低", "基础体验够用"], ["降噪、音质和做工有取舍"], "预算策略", ["预算优先", "学生党"]),
            ]
        return [
            self._arch("高性价比型", ["预算优先", "实用", "入门"], ["价格门槛低", "适合先试用"], ["性能、材质或体验可能有取舍"], "预算策略", ["预算优先", "新手"]),
            self._arch("品质体验型", ["品质", "做工", "稳定"], ["体验更稳定", "长期使用更安心"], ["价格通常更高"], "品质偏好", ["预算不敏感", "长期使用"]),
            self._arch("专业功能型", ["专业", "功能", "高性能"], ["核心功能更强", "适合明确需求"], ["学习成本或价格更高"], "核心功能", ["专业需求", "重度使用"]),
            self._arch("便携轻量型", ["便携", "轻量", "日常"], ["携带和日常使用更轻松", "适合通勤"], ["极致性能可能不足"], "使用场景", ["通勤", "日常"]),
            self._arch("安全稳妥型", ["安全", "售后", "口碑"], ["更重视售后和稳定性", "降低踩雷风险"], ["参数可能不是最激进"], "风险边界", ["保守决策", "送礼"]),
        ]

    def _arch(
        self,
        name: str,
        claims: list[str],
        strengths: list[str],
        weaknesses: list[str],
        dimension: str,
        target_users: list[str],
    ) -> dict:
        return {
            "name": name,
            "claims": claims,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "dimension": dimension,
            "target_users": target_users,
        }

    def _remember_dynamic_source(self, source: HotlistSource) -> None:
        path = self.data_dir / "market_sources" / "jd_dynamic_sources.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = []
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                existing = []
        by_id = {item.get("id"): item for item in existing if item.get("id")}
        by_id[source.id] = self._source_payload(source) | {
            "heading_keywords": source.heading_keywords,
        }
        path.write_text(
            json.dumps(list(by_id.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _brand_from_title(self, title: str) -> str:
        parts = title.split()
        return parts[0][:24] if parts else title[:12]

    def _category_keyword(self, query: str) -> str:
        text = re.sub(r"[，。！？,.!?]", " ", query)
        match = re.search(r"(?:买|选|推荐|想要|想找)([^ ]{2,18})", text)
        if match:
            text = match.group(1)
        for token in ["我想", "我想买", "不知道", "怎么选", "哪个", "适合", "通勤用", "日常用", "推荐", "买"]:
            text = text.replace(token, "")
        text = text.strip() or query.strip()
        return text[:20] or "商品"

    def _keyword_from_source_name(self, name: str) -> str:
        match = re.search(r"京东(.+?)公开排行榜", name)
        if match:
            return match.group(1)
        return name.replace("京东", "").replace("排行榜", "").strip() or "商品"

    def _claim_terms(self, title: str) -> list[str]:
        candidates = [
            "主动降噪", "通话降噪", "蓝牙", "长续航", "快速充电", "开放式",
            "不入耳", "半入耳", "耳夹式", "骨传导", "运动跑步", "高音质",
            "翻译", "空间音频", "华为", "联想", "苹果适配",
        ]
        hits = [term for term in candidates if term in title]
        return list(dict.fromkeys(hits))
