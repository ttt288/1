import hashlib
import json
import re
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import monotonic
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.schemas.market import RankingEntry, RankingSnapshot, RefreshState


JD_URL = "https://www.jd.com/phb/key_699402c85b220b9a9f13.html"
TTL_SECONDS = 6 * 3600
RETRY_SECONDS = 300
MAX_BYTES = 2 * 1024 * 1024


def utc_now() -> datetime:
    return datetime.now(UTC)


def extract_spec(title: str) -> str | None:
    # Ambiguous multipacks require manual checking; do not guess total weight.
    if re.search(r"[×*+]", title):
        return None
    values = re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(kg|公斤|千克|斤|g|克)(?![A-Za-z])", title, re.I)
    if len(values) != 1:
        return None
    number, unit = values[0]
    grams = Decimal(number) * {"kg": 1000, "公斤": 1000, "千克": 1000, "斤": 500, "g": 1, "克": 1}[unit.lower()]
    return f"{grams.normalize():f}g" if grams > 0 else None


def parse_ranking(html: str, expected_heading_keywords: list[str] | None = None) -> list[RankingEntry]:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("h1")
    title = soup.select_one("title")
    description = soup.select_one('meta[name="description"]')
    heading_text = " ".join([
        heading.get_text(" ", strip=True) if heading else "",
        title.get_text(" ", strip=True) if title else "",
        description.get("content", "") if description else "",
    ])
    expected = expected_heading_keywords or ["京东", "猫粮", "排行榜"]
    if not heading_text or not all(keyword in heading_text for keyword in expected):
        raise ValueError("未识别到预期京东榜单，可能返回了验证页或页面结构已变化")
    entries = []
    for row in soup.select(".content-item"):
        rank = row.select_one(".rank span")
        link = row.select_one(".product-details .detail a")
        if not rank or not link:
            raise ValueError("榜单条目缺少排名或商品链接")
        url = urlparse(link.get("href", ""))
        sku = re.fullmatch(r"/(\d{5,20})\.html", url.path)
        if url.scheme != "https" or url.netloc != "item.jd.com" or not sku:
            raise ValueError("榜单商品链接格式异常")
        title = link.get_text(" ", strip=True)
        review = row.select_one(".evaluate-num")
        price_node = row.select_one(".price-rmb")
        price_text = price_node.get_text(strip=True) if price_node else ""
        image = row.select_one(".product-img img")
        image_url = image.get("src", "") if image else ""
        if image_url.startswith("//"):
            image_url = "https:" + image_url
        image_parts = urlparse(image_url)
        if image_parts.scheme != "https" or not (image_parts.hostname or "").endswith(".360buyimg.com"):
            image_url = None
        entries.append(RankingEntry(
            rank=int(rank.get_text(strip=True)), jd_sku=sku[1], title=title,
            product_url=f"https://item.jd.com/{sku[1]}.html", image_url=image_url,
            spec=extract_spec(title), review_count_label=review.get_text(strip=True) if review else None,
            price=float(price_text) if re.fullmatch(r"\d+(?:\.\d{1,2})?", price_text) else None,
        ))
    entries.sort(key=lambda item: item.rank)
    if not 10 <= len(entries) <= 20 or [item.rank for item in entries] != list(range(1, len(entries) + 1)):
        raise ValueError("榜单不足 10 条或排名不连续，已保留旧快照")
    if len({item.jd_sku for item in entries}) != len(entries):
        raise ValueError("榜单包含重复 SKU，已保留旧快照")
    return entries


def match_entry(entry: RankingEntry, products: list) -> tuple[object | None, str]:
    same_sku = [product for product in products if product.jd_sku == entry.jd_sku]
    if len(same_sku) != 1:
        return None, "本地尚无唯一对应 SKU"
    product = same_sku[0]
    if not entry.spec or extract_spec(product.spec) != entry.spec:
        return None, "包装规格不一致或尚待核验"
    return product, "SKU 与包装规格一致；配方和参考价仍按本地资料"


class JDRankingService:
    def __init__(
        self,
        data_dir: Path,
        fetcher=None,
        clock=utc_now,
        source_key: str = "jd_pet_food",
        source_url: str = JD_URL,
        source_name: str = "京东猫粮公开排行榜",
        expected_heading_keywords: list[str] | None = None,
    ):
        self.source_key = source_key
        self.source_url = source_url
        self.source_name = source_name
        self.expected_heading_keywords = expected_heading_keywords
        self.directory = data_dir / "market_sources" / source_key
        self.snapshot_path = self.directory / "current.json"
        self.state_path = self.directory / "refresh_state.json"
        self.fetcher = fetcher or self._fetch
        self.clock = clock
        self._lock = Lock()

    def _fetch(self) -> bytes:
        # Fixed source, bounded response, no redirects, credentials, or LLM calls.
        with httpx.Client(timeout=httpx.Timeout(10, connect=4), follow_redirects=False) as client:
            with client.stream("GET", self.source_url, headers={"User-Agent": "ShoppingDecisionResearch/1.0", "Accept": "text/html"}) as response:
                response.raise_for_status()
                chunks, size = [], 0
                started = monotonic()
                for chunk in response.iter_bytes():
                    if monotonic() - started > 12:
                        raise ValueError("榜单读取超过时限，已保留旧快照")
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError("榜单响应超过大小限制")
                    chunks.append(chunk)
                return b"".join(chunks)

    def _read(self, path, schema):
        if not path.exists():
            return None
        return schema.model_validate_json(path.read_text(encoding="utf-8"))

    def snapshot(self) -> RankingSnapshot | None:
        return self._read(self.snapshot_path, RankingSnapshot)

    def _write(self, path: Path, content: bytes):
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as file:
            temp = Path(file.name)
            file.write(content)
        try:
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

    def status(self) -> dict:
        snapshot = self.snapshot()
        state = self._read(self.state_path, RefreshState) or RefreshState()
        now = self.clock()
        expires = datetime.fromisoformat(snapshot.observed_at) + timedelta(seconds=TTL_SECONDS) if snapshot else None
        return {
            **state.model_dump(), "snapshot": snapshot.model_dump() if snapshot else None,
            "stale": expires is None or now >= expires,
            "expires_at": expires.isoformat() if expires else None,
            "refreshing": self._lock.locked(), "ttl_seconds": TTL_SECONDS,
            "llm_calls": 0,
        }

    def refresh(self, force=False) -> dict:
        if not self._lock.acquire(blocking=False):
            return {**self.status(), "refresh_result": "busy"}
        try:
            state = self._read(self.state_path, RefreshState) or RefreshState()
            previous = self.snapshot()
            now = self.clock()
            if state.last_checked_at:
                elapsed = (now - datetime.fromisoformat(state.last_checked_at)).total_seconds()
                cooldown = 30 if force else (RETRY_SECONDS if state.status == "failed" else TTL_SECONDS)
                if elapsed < cooldown:
                    return {**self.status(), "refresh_result": "cached", "retry_after_seconds": max(1, int(cooldown - elapsed))}
            checked = now.isoformat()
            state.last_checked_at = checked
            try:
                raw = self.fetcher()
                if len(raw) > MAX_BYTES:
                    raise ValueError("榜单响应超过大小限制")
                entries = parse_ranking(raw.decode("utf-8-sig"), self.expected_heading_keywords)
                content = json.dumps([entry.model_dump() for entry in entries], ensure_ascii=False, sort_keys=True).encode()
                content_hash = hashlib.sha256(content).hexdigest()
                raw_hash = hashlib.sha256(raw).hexdigest()
                changed = not previous or previous.content_hash != content_hash
                observed = self.clock().isoformat()
                raw_file = f"raw/{raw_hash}.html"
                snapshot = RankingSnapshot(
                    source_url=self.source_url, source_name=self.source_name, observed_at=observed,
                    content_changed_at=observed if changed else previous.content_changed_at,
                    content_hash=content_hash, raw_sha256=raw_hash, raw_file=raw_file, entries=entries,
                )
                self._write(self.directory / raw_file, raw)
                self._write(self.directory / "history" / f"{content_hash}.json", snapshot.model_dump_json(indent=2).encode())
                self._write(self.snapshot_path, snapshot.model_dump_json(indent=2).encode())
                state.last_success_at = observed
                state.status = "updated" if changed else "unchanged"
                state.error = None
            except (httpx.HTTPError, ValueError, UnicodeError, OSError) as exc:
                state.status = "failed"
                state.error = str(exc)[:300] if isinstance(exc, ValueError) else "网络请求或快照存储失败，请稍后重试"
            self._write(self.state_path, state.model_dump_json(indent=2).encode())
            return {**self.status(), "refresh_result": state.status}
        finally:
            self._lock.release()
