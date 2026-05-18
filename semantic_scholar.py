import hashlib
import json
import logging
import os
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.semanticscholar.org/graph/v1"
FIELDS = (
    "title,abstract,authors,year,citationCount,url,"
    "isOpenAccess,openAccessPdf,fieldsOfStudy,venue,"
    "referenceCount,externalIds"
)
REQUEST_TIMEOUT = (5, 10)


@dataclass
class Paper:
    paper_id: str
    title: str = ""
    abstract: str = ""
    authors: list[str] = field(default_factory=list)
    year: int = 0
    citation_count: int = 0
    url: str = ""
    open_access_url: str = ""
    fields_of_study: list[str] = field(default_factory=list)
    venue: str = ""


class PaperCache:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._ensure_table()
        return self._conn

    def _ensure_table(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS paper_cache ("
            "cache_key TEXT PRIMARY KEY,"
            "data TEXT NOT NULL,"
            "created_at REAL NOT NULL"
            ")"
        )
        self.conn.commit()

    def _make_key(self, *parts: str) -> str:
        raw = "|".join(parts)
        return hashlib.md5(raw.encode()).hexdigest()

    def get(self, cache_key: str, ttl_seconds: int = 3600) -> Optional[str]:
        try:
            row = self.conn.execute(
                "SELECT data, created_at FROM paper_cache WHERE cache_key=?",
                (cache_key,),
            ).fetchone()
            if row is None:
                return None
            data, created_at = row
            if time.time() - created_at > ttl_seconds:
                self.conn.execute(
                    "DELETE FROM paper_cache WHERE cache_key=?", (cache_key,)
                )
                self.conn.commit()
                return None
            return data
        except Exception:
            return None

    def set(self, cache_key: str, json_data: str) -> None:
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO paper_cache (cache_key, data, created_at) VALUES (?, ?, ?)",
                (cache_key, json_data, time.time()),
            )
            self.conn.commit()
        except Exception:
            pass


class SemanticScholarConnector:
    def __init__(self, cache: PaperCache, api_key: Optional[str] = None) -> None:
        self._cache = cache
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "Shiori-Agent/1.0 (Academic Research Assistant; mailto:shiori@example.com)"
        })
        self._has_api_key = bool(api_key)
        if api_key:
            self._session.headers["x-api-key"] = api_key
        self._last_error: Optional[str] = None

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    # 模块级速率控制
    _last_request_time: float = 0.0
    _cooldown_until: float = 0.0  # 全局冷却，429 后 30s 内所有请求直接拒绝

    def _min_interval(self) -> float:
        return 1.0 if self._has_api_key else 2.0

    def _get(self, endpoint: str, params: dict) -> Tuple[dict, bool]:
        cache_key = self._cache._make_key(endpoint, json.dumps(params, sort_keys=True))
        cached = self._cache.get(cache_key)
        if cached is not None:
            try:
                return json.loads(cached), True
            except Exception:
                pass

        # 全局冷却检查：收到 429 后 30s 内不发出任何新请求
        now = time.time()
        if now < SemanticScholarConnector._cooldown_until:
            self._last_error = "API 限流中，请等待冷却后再试"
            return {}, False

        # 速率控制：强制请求间隔
        interval = self._min_interval()
        elapsed = now - SemanticScholarConnector._last_request_time
        if elapsed < interval:
            time.sleep(interval - elapsed)

        url = f"{BASE_URL}{endpoint}"
        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                SemanticScholarConnector._last_request_time = time.time()
                resp = self._session.get(url, params=params, timeout=REQUEST_TIMEOUT)
                last_status = resp.status_code
                if resp.status_code == 429:
                    SemanticScholarConnector._cooldown_until = time.time() + 30
                    if attempt < max_attempts - 1:
                        wait = 5 * (attempt + 1)
                        logger.info("Rate limited, cooling down 60s (attempt %d)", attempt + 1)
                        time.sleep(wait)
                        continue
                    self._last_error = "API 限流，请等待冷却后重试"
                    return {}, False
                if resp.status_code == 404:
                    return {}, True
                resp.raise_for_status()
                data = resp.json()
                self._cache.set(cache_key, json.dumps(data, ensure_ascii=False))
                self._last_error = None
                return data, True
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else "?"
                logger.warning("HTTP error %s: %s", status, endpoint)
                self._last_error = f"HTTP {status}"
                return {}, False
            except requests.RequestException as e:
                logger.warning("Request failed (%s): %s (attempt %d)", type(e).__name__, endpoint, attempt + 1)
                if attempt < max_attempts - 1:
                    time.sleep(2 * (attempt + 1))
                    continue
                self._last_error = f"网络不可达 ({type(e).__name__})"
                return {}, False

        self._last_error = "API 暂时不可用"
        logger.warning("All retries exhausted for %s", endpoint)
        return {}, False

    def _parse_paper(self, item: dict) -> Paper:
        authors = [a.get("name", "") for a in item.get("authors", [])]
        oa = item.get("openAccessPdf") or {}
        oa_url = oa.get("url", "") if isinstance(oa, dict) else ""
        ext_id = item.get("externalIds", {}) or {}
        paper_id = (
            item.get("paperId")
            or str(ext_id.get("CorpusId", ""))
            or ext_id.get("DOI")
            or ""
        )
        return Paper(
            paper_id=str(paper_id),
            title=item.get("title") or "",
            abstract=item.get("abstract") or "",
            authors=authors,
            year=item.get("year") or 0,
            citation_count=item.get("citationCount") or 0,
            url=item.get("url") or "",
            open_access_url=oa_url,
            fields_of_study=item.get("fieldsOfStudy") or [],
            venue=item.get("venue") or "",
        )

    def _parse_list(self, data: dict) -> list[Paper]:
        items = data.get("data") or []
        return [self._parse_paper(item) for item in items]

    def search(
        self,
        query: str,
        limit: int = 5,
        year_from: Optional[int] = None,
        year_to: Optional[int] = None,
    ) -> Tuple[list[Paper], bool]:
        params: dict = {"query": query, "limit": min(limit, 10), "fields": FIELDS}
        if year_from:
            params["year"] = f"{year_from}-"
        if year_to:
            year_range = params.get("year", "")
            params["year"] = f"{year_range}{year_to}" if year_range else f"-{year_to}"
        data, ok = self._get("/paper/search", params)
        return self._parse_list(data), ok

    def get_paper(self, paper_id: str) -> Tuple[Optional[Paper], bool]:
        pid = paper_id.strip()
        if not pid:
            return None, False
        data, ok = self._get(f"/paper/{pid}", {"fields": FIELDS})
        if not ok:
            return None, False
        if not data or data.get("error"):
            return None, True
        return self._parse_paper(data), True

    def get_citations(self, paper_id: str, limit: int = 5) -> Tuple[list[Paper], bool]:
        pid = paper_id.strip()
        if not pid:
            return [], False
        params = {"limit": min(limit, 10), "fields": FIELDS}
        data, ok = self._get(f"/paper/{pid}/citations", params)
        if not ok:
            return [], False
        items = data.get("data") or []
        return [self._parse_paper(item.get("citingPaper") or item) for item in items], True

    def get_references(self, paper_id: str, limit: int = 5) -> Tuple[list[Paper], bool]:
        pid = paper_id.strip()
        if not pid:
            return [], False
        params = {"limit": min(limit, 10), "fields": FIELDS}
        data, ok = self._get(f"/paper/{pid}/references", params)
        if not ok:
            return [], False
        items = data.get("data") or []
        return [self._parse_paper(item.get("citedPaper") or item) for item in items], True


def create_connector(api_key: Optional[str] = None) -> SemanticScholarConnector:
    db_dir = os.path.dirname(os.path.abspath(__file__))
    cache_path = os.path.join(db_dir, "scholar_cache.db")
    key = api_key or os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    return SemanticScholarConnector(PaperCache(cache_path), api_key=key)
