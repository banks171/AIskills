#!/usr/bin/env python3
"""
Feed 数据源多协议探测脚本

用法：
  python3 probe_source.py --mode rss --url "https://example.com/rss"
  python3 probe_source.py --mode api --url "https://api.example.com/v1/news" --headers '{"Authorization": "Bearer xxx"}'
  python3 probe_source.py --mode web --url "https://example.com" --selector ".article-body"
  python3 probe_source.py --mode discover --url "https://example.com" --name "example"

输出 JSON 结构：
{
  "mode": "rss|api|web",
  "url": "...",
  "success": true|false,
  "http_code": 200,
  "content_type": "...",
  "size_bytes": 12345,
  "diagnosis": "通过|反爬拦截|SPA需JS渲染|付费墙|...",
  "grade": "A|B|C|D",
  "details": { ... },
  "sample_path": "...",
  "note": "RSS可达；50项含description摘要；B级",
  "fields": { ... }
}
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
from xml.etree import ElementTree


# ═══════════════════════════════════════════════════════════════
# Section 1: 路径常量
# ═══════════════════════════════════════════════════════════════

SCRIPT_DIR = Path(__file__).parent
PATTERNS_FILE = SCRIPT_DIR.parent / "references" / "source_patterns.json"
HISTORY_FILE = SCRIPT_DIR.parent / "references" / "probe_history.jsonl"


# ═══════════════════════════════════════════════════════════════
# Section 2: 命名常量 & 阈值
# ═══════════════════════════════════════════════════════════════

# ── 内容阈值 ──
MIN_BODY_CHARS_PASS = 200       # WEB 通过最低正文字符数
FULL_CONTENT_CHARS = 500        # content:encoded "全文" 判断阈值
RSS_DESCRIPTION_MIN = 50        # description "有摘要" 判断阈值
API_CONTENT_MIN_FIELDS = 3      # API 无正文字段时最低字段数
GRADE_THRESHOLDS = (0.85, 0.55, 0.25)  # A/B/C 质量评分边界

# ── SPA 检测阈值 ──
SPA_SIZE_NOSCRIPT = 5000        # <noscript> 存在时的 SPA 判定上限
SPA_SIZE_FRAMEWORK = 3000       # __NEXT_DATA__ 等框架骨架判定上限
JSONLD_MIN_BODY = 200           # JSON-LD articleBody 最短有效长度
NEXTJS_MIN_CONTENT = 200        # __NEXT_DATA__ 内容最短有效长度

# ── HTTP ──
DEFAULT_TIMEOUT = 15
DISCOVERY_TIMEOUT = 8
SITEMAP_TIMEOUT = 10
MAX_RETRIES = 2
RETRY_BACKOFF = 1.0             # 重试间隔秒数（指数退避基数）
RETRYABLE_CODES = frozenset({429, 500, 502, 503, 504})

# ── 评级 ──
GRADE_ORDER = {"A": 0, "B": 1, "C": 2, "D": 3}

# ── type 选择优先级（正文完整度相同时）──
# 正文完整度优先；同等完整度下 API > RSS > WEB
TYPE_PRIORITY = {"api": 0, "rss": 1, "web": 2}

# ── kind 正文完整度：API 结构化字段匹配模式 ──
KIND_FIELD_PATTERNS: dict[str, dict[str, list[str]]] = {
    "filing": {
        "id": ["document_number", "id", "filing_id", "accession_number",
               "cik", "case_number", "docket_number", "number"],
        "type": ["type", "category", "form_type", "document_type", "action"],
        "date": ["publication_date", "date", "filed_date", "filing_date",
                 "created_at", "effective_date", "updated_at"],
        "agency": ["agencies", "agency", "issuer", "company", "filer",
                   "department", "bureau", "organization"],
    },
    "calendar": {
        "date": ["date", "event_date", "release_date", "start_date",
                 "report_date", "earnings_date", "ipo_date"],
        "event": ["event", "title", "name", "description", "indicator",
                  "report", "announcement", "symbol"],
    },
    "metric": {
        "value": ["value", "actual", "price", "close", "rate", "amount",
                  "count", "estimate", "forecast", "previous", "change"],
        "date": ["date", "period", "timestamp", "time", "year", "quarter",
                 "month", "reportDate"],
        "subject": ["indicator", "symbol", "ticker", "country", "subject",
                    "series_id", "metric", "name", "currency"],
    },
}

# ── User-Agent ──
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

# ── RSS 路径 fallback（source_patterns.json 不可用时使用） ──
_FALLBACK_RSS_PATHS = [
    "/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml",
    "/index.xml", "/rss/v2.xml", "/feeds/posts/default",
    "/news/rss", "/rss/news", "/feed/",
]


# ═══════════════════════════════════════════════════════════════
# Section 3: FetchResult + fetch_url（含重试）
# ═══════════════════════════════════════════════════════════════

@dataclasses.dataclass
class FetchResult:
    """HTTP 请求结果（替代原始 4 元组，保留错误信息）"""
    body: bytes = b""
    status: int = 0
    content_type: str = ""
    size: int = 0
    error: str | None = None        # None = 成功
    error_type: str | None = None   # timeout / dns / connection / http / ssl
    elapsed_ms: int = 0


def fetch_url(
    url: str,
    headers: dict[str, str] | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = MAX_RETRIES,
) -> FetchResult:
    """发送 HTTP 请求，支持重试和错误分类"""
    req_headers = {
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
                  "application/json;q=0.8,*/*;q=0.7",
        "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
    }
    if headers:
        req_headers.update(headers)

    req = urllib.request.Request(url, headers=req_headers)
    last_result = FetchResult()

    for attempt in range(1 + retries):
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read()
                return FetchResult(
                    body=body,
                    status=resp.status,
                    content_type=resp.headers.get("Content-Type", ""),
                    size=len(body),
                    elapsed_ms=int((time.monotonic() - t0) * 1000),
                )
        except urllib.error.HTTPError as e:
            body = b""
            try:
                body = e.read()
            except Exception:
                pass
            last_result = FetchResult(
                body=body,
                status=e.code,
                content_type=e.headers.get("Content-Type", "") if e.headers else "",
                size=len(body),
                error=str(e),
                error_type="http",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
            if e.code not in RETRYABLE_CODES:
                return last_result
        except urllib.error.URLError as e:
            reason = str(e.reason) if hasattr(e, "reason") else str(e)
            if "timed out" in reason.lower() or "timeout" in reason.lower():
                etype = "timeout"
            elif "name or service not known" in reason.lower() or "nodename" in reason.lower():
                etype = "dns"
            elif "ssl" in reason.lower() or "certificate" in reason.lower():
                etype = "ssl"
            else:
                etype = "connection"
            last_result = FetchResult(
                error=reason,
                error_type=etype,
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )
        except Exception as e:
            last_result = FetchResult(
                error=str(e),
                error_type="connection",
                elapsed_ms=int((time.monotonic() - t0) * 1000),
            )

        # 指数退避
        if attempt < retries:
            delay = RETRY_BACKOFF * (2 ** attempt)
            _log("WARN", f"重试 {attempt + 1}/{retries}（{delay:.1f}s后）: {last_result.error}")
            time.sleep(delay)

    return last_result


# ═══════════════════════════════════════════════════════════════
# Section 4: 检测工具函数
# ═══════════════════════════════════════════════════════════════

# ── 反爬检测 ──

ANTI_CRAWL_SIGNATURES = [
    ("cloudflare", "Cloudflare"),
    ("cf-ray", "Cloudflare"),
    ("datadome", "DataDome"),
    ("perimeterx", "PerimeterX"),
    ("akamai", "Akamai"),
    ("incapsula", "Incapsula"),
]


def detect_anti_crawl(body_text: str, http_code: int) -> str | None:
    """检测反爬标识，返回 '{Name} {code}' 或 None"""
    if http_code in (403, 401, 503):
        lower = body_text.lower()
        for sig, name in ANTI_CRAWL_SIGNATURES:
            if sig in lower:
                return f"{name} {http_code}"
        return f"HTTP {http_code}"
    return None


# ── 付费墙检测 ──

PAYWALL_KEYWORDS = [
    "subscribe to read", "subscribe to continue", "premium content",
    "sign in to read", "create an account", "paywall",
    "members only", "subscriber-only",
]


def detect_paywall(body_text: str) -> str | None:
    """检测付费墙关键词"""
    lower = body_text.lower()
    for kw in PAYWALL_KEYWORDS:
        if kw in lower:
            return f'Paywall "{kw}"'
    return None


# ── SPA 检测 ──

def detect_spa(body_text: str, size: int) -> str | None:
    """检测 SPA 空壳（小页面 + 框架骨架标识）"""
    if size < SPA_SIZE_NOSCRIPT and "<noscript>" in body_text:
        return "SPA骨架屏无内容，需JS渲染"
    if size < SPA_SIZE_FRAMEWORK and ("__NEXT_DATA__" in body_text or "window.__APP" in body_text):
        return "SPA骨架屏无内容，需JS渲染"
    return None


# ── CMS/框架检测 ──

CMS_SIGNATURES = [
    ("drupal", "Drupal"),
    ("wp-content", "WordPress"),
    ("wordpress", "WordPress"),
    ("webflow", "Webflow"),
    ("__NEXT_DATA__", "Next.js"),
    ("_next/static", "Next.js"),
    ("nuxt", "Nuxt.js"),
    ("gatsby", "Gatsby"),
    ("wix.com", "Wix"),
    ("squarespace", "Squarespace"),
    ("weebly", "Weebly"),
    ("sitefinity", "Sitefinity"),
    ("aem", "AEM"),
]


def detect_cms(body_text: str) -> str | None:
    """检测 CMS/框架平台"""
    lower = body_text[:50000].lower()
    for sig, name in CMS_SIGNATURES:
        if sig.lower() in lower:
            return name
    return None


# ── Next.js JSON 路径检测 ──

def detect_nextjs_json_path(body_text: str) -> str | None:
    """检测 __NEXT_DATA__ 并尝试找到内容路径"""
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', body_text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(1))
        props = data.get("props", {}).get("pageProps", {})
        for key in ["article", "post", "content", "story", "detailData", "data"]:
            if key in props:
                val = props[key]
                if isinstance(val, dict):
                    for subkey in ["content", "body", "text", "contentDetail", "articleBody"]:
                        if subkey in val and len(str(val[subkey])) > NEXTJS_MIN_CONTENT:
                            return f"props.pageProps.{key}.{subkey}"
                elif isinstance(val, str) and len(val) > NEXTJS_MIN_CONTENT:
                    return f"props.pageProps.{key}"
        return "__NEXT_DATA__"
    except (json.JSONDecodeError, AttributeError):
        return "__NEXT_DATA__"


# ── JSON-LD 检测 ──

def detect_jsonld_article(body_text: str) -> int | None:
    """检测 JSON-LD 中的 articleBody，返回字符数或 None"""
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', body_text, re.DOTALL):
        try:
            data = json.loads(m.group(1))
            if isinstance(data, list):
                data = data[0]
            if isinstance(data, dict) and "articleBody" in data:
                body_len = len(data["articleBody"])
                if body_len > JSONLD_MIN_BODY:
                    return body_len
        except (json.JSONDecodeError, TypeError, IndexError):
            continue
    return None


# ── XML/RSS 响应判断 ──

def _is_xml_response(ctype: str, body_text_head: str) -> bool:
    """判断响应是否为 XML/RSS/Atom"""
    return (
        any(x in ctype.lower() for x in ["xml", "rss", "atom"])
        or body_text_head.lstrip().startswith("<?xml")
        or "<rss" in body_text_head
        or "<feed" in body_text_head
    )


# ═══════════════════════════════════════════════════════════════
# Section 5: 内容分析工具
# ═══════════════════════════════════════════════════════════════

def count_text_chars(text: str) -> int:
    """统计纯文本字符数（去除 HTML 标签和多余空白）"""
    clean = re.sub(r"<[^>]+>", "", text)
    clean = re.sub(r"\s+", " ", clean).strip()
    return len(clean)


def count_article_links(body_text: str, base_url: str) -> int:
    """统计页面中的文章链接数"""
    domain = urlparse(base_url).netloc
    hrefs = re.findall(r'<a[^>]+href="([^"]*)"', body_text, re.IGNORECASE)
    article_patterns = [
        r'/article/', r'/news/', r'/story/', r'/post/',
        r'/\d{4}/\d{2}/', r'/p/', r'/a/', r'\.html$', r'\.shtml$',
    ]
    count = 0
    for href in hrefs:
        if domain in href or href.startswith("/"):
            for pat in article_patterns:
                if re.search(pat, href):
                    count += 1
                    break
    return count


def detect_rss_format(root_tag: str) -> str:
    """检测 RSS 格式变体"""
    tag = root_tag.lower() if root_tag else ""
    if "rdf" in tag:
        return "RDF 1.0"
    if "feed" in tag:
        return "Atom"
    return "RSS 2.0"


# ── Gate + Quality 双层评估配置 ──

# Gate 类型:
#   "bool"            — details[key] 为真
#   "or"              — details 中任一 key 为真
#   "gt"              — details[key] > min
#   "field_group_hit" — 该 kind 的指定字段组至少命中一个
#
# Signal scorer 类型:
#   "length"              — 分段线性映射 (low/mid/high)
#   "bool" / "bool_inverse" — 布尔 / 反布尔
#   "ratio_inverse"       — 反比例 (good→1.0, bad→0.0)
#   "paragraph_depth"     — 段落深度阶梯
#   "field_group_coverage" — API 字段组覆盖率
#   "item_count_ratio"    — item 数量归一化

KIND_MODE_CONFIG: dict[str, dict[str, dict]] = {
    "news": {
        "rss": {
            "gates": [
                ("has_title",          "bool",  {"key": "has_title"}),
                ("has_content_source", "or",    {"keys": ["has_content_tag", "has_description"]}),
                ("has_link",           "bool",  {"key": "has_link"}),
            ],
            "signals": [
                ("body_richness",    3.0, "length",         {"low": 50, "mid": 300, "high": 1000}),
                ("structural_depth", 2.0, "paragraph_depth", {"mode": "rss"}),
                ("no_truncation",    2.0, "bool_inverse",   {"key": "truncation_marker"}),
                ("item_consistency", 1.0, "ratio_inverse",  {"key": "item_consistency", "good": 0.0, "bad": 2.0}),
            ],
            "threshold": 0.45,
        },
        "api": {
            "gates": [
                ("has_content_source", "bool", {"key": "has_content_field"}),
            ],
            "signals": [
                ("body_richness", 3.0, "length",        {"low": 50, "mid": 300, "high": 1000}),
                ("field_quality", 1.0, "ratio_inverse",  {"key": "empty_field_ratio", "good": 0.0, "bad": 0.5}),
            ],
            "threshold": 0.40,
        },
        "web": {
            "gates": [
                ("has_title",          "bool", {"key": "has_title"}),
                ("has_content_source", "gt",   {"key": "char_count", "min": 50}),
            ],
            "signals": [
                ("body_richness",        3.0, "length",         {"low": 100, "mid": 500, "high": 1500}),
                ("structural_depth",     2.0, "paragraph_depth", {"mode": "web"}),
                ("no_truncation",        2.0, "bool_inverse",   {"key": "truncation_marker"}),
                ("no_paywall",           2.0, "bool_inverse",   {"key": "paywall"}),
                ("content_signal_ratio", 1.0, "length",         {"low": 0.05, "mid": 0.15, "high": 0.3, "key": "content_to_page_ratio"}),
                ("has_time",             1.0, "bool",           {"key": "has_time"}),
                ("has_author",           1.0, "bool",           {"key": "has_author"}),
            ],
            "threshold": 0.45,
        },
    },
    "filing": {
        "rss": {
            "gates": [
                ("has_title",      "bool", {"key": "has_title"}),
                ("has_identifier", "bool", {"key": "has_link"}),
            ],
            "signals": [
                ("body_richness", 2.0, "length",       {"low": 20, "mid": 80, "high": 300}),
                ("no_truncation", 1.0, "bool_inverse", {"key": "truncation_marker"}),
            ],
            "threshold": 0.30,
        },
        "api": {
            "gates": [
                ("has_identifier", "field_group_hit", {"kind": "filing", "group": "id"}),
            ],
            "signals": [
                ("field_coverage", 3.0, "field_group_coverage", {"kind": "filing"}),
                ("field_quality",  2.0, "ratio_inverse",        {"key": "empty_field_ratio", "good": 0.0, "bad": 0.5}),
                ("has_date",       1.0, "bool",                 {"key": "has_date_field"}),
            ],
            "threshold": 0.45,
        },
        "web": {
            "gates": [
                ("has_title", "bool", {"key": "has_title"}),
            ],
            "signals": [
                ("body_richness",  3.0, "length",       {"low": 30, "mid": 100, "high": 500}),
                ("has_structured", 1.5, "bool",         {"key": "has_structured_data"}),
                ("no_truncation",  1.0, "bool_inverse", {"key": "truncation_marker"}),
            ],
            "threshold": 0.35,
        },
    },
    "calendar": {
        "rss": {
            "gates": [
                ("has_date",  "bool", {"key": "has_pubdate"}),
                ("has_event", "bool", {"key": "has_title"}),
            ],
            "signals": [
                ("body_richness", 0.5, "length", {"low": 10, "mid": 50, "high": 200}),
            ],
            "threshold": 0.20,
        },
        "api": {
            "gates": [
                ("has_date",  "bool",            {"key": "has_date_field"}),
                ("has_event", "field_group_hit",  {"kind": "calendar", "group": "event"}),
            ],
            "signals": [
                ("field_coverage", 3.0, "field_group_coverage", {"kind": "calendar"}),
                ("field_quality",  1.0, "ratio_inverse",        {"key": "empty_field_ratio", "good": 0.0, "bad": 0.5}),
            ],
            "threshold": 0.40,
        },
        "web": {
            "gates": [
                ("has_date", "bool", {"key": "has_time"}),
            ],
            "signals": [
                ("body_richness", 1.5, "length", {"low": 20, "mid": 100, "high": 500}),
                ("has_title",     1.0, "bool",   {"key": "has_title"}),
            ],
            "threshold": 0.30,
        },
    },
    "social": {
        "rss": {
            "gates": [
                ("has_content", "bool", {"key": "has_description"}),
            ],
            "signals": [
                ("body_richness", 2.0, "length",          {"low": 5, "mid": 30, "high": 200}),
                ("item_count",    0.5, "item_count_ratio", {}),
            ],
            "threshold": 0.30,
        },
        "api": {
            "gates": [
                ("has_content", "bool", {"key": "has_content_field"}),
            ],
            "signals": [
                ("body_richness", 2.0, "length",          {"low": 5, "mid": 30, "high": 200}),
                ("field_quality", 1.0, "ratio_inverse",    {"key": "empty_field_ratio", "good": 0.0, "bad": 0.5}),
                ("item_count",    0.5, "item_count_ratio", {}),
            ],
            "threshold": 0.30,
        },
        "web": {
            "gates": [
                ("has_content", "gt", {"key": "char_count", "min": 5}),
            ],
            "signals": [
                ("body_richness", 2.0, "length", {"low": 5, "mid": 30, "high": 200}),
            ],
            "threshold": 0.25,
        },
    },
    "metric": {
        "api": {
            "gates": [
                ("has_numeric", "bool", {"key": "has_numeric_field"}),
                ("has_date",    "bool", {"key": "has_date_field"}),
            ],
            "signals": [
                ("field_coverage", 3.0, "field_group_coverage", {"kind": "metric"}),
                ("field_quality",  1.0, "ratio_inverse",        {"key": "empty_field_ratio", "good": 0.0, "bad": 0.5}),
            ],
            "threshold": 0.40,
        },
    },
}


# ── Gate 评估器 ──

def _evaluate_gate(gate: tuple, details: dict, kind: str) -> bool:
    """评估单个 gate 是否通过"""
    _name, gate_type, params = gate
    if gate_type == "bool":
        return bool(details.get(params["key"]))
    if gate_type == "or":
        return any(details.get(k) for k in params["keys"])
    if gate_type == "gt":
        return details.get(params["key"], 0) > params.get("min", 0)
    if gate_type == "field_group_hit":
        api_fields = [f.lower() for f in details.get("first_item_fields", [])]
        group_keys = KIND_FIELD_PATTERNS.get(params["kind"], {}).get(params["group"], [])
        return any(f in api_fields for f in group_keys)
    return False


# ── 评分函数库 ──

def _score_length(val: float, low: float, mid: float, high: float) -> float:
    """分段线性映射: <low->0.0, low-mid->0.0-0.6, mid-high->0.6-1.0, >=high->1.0"""
    if val >= high:
        return 1.0
    if val >= mid:
        return 0.6 + 0.4 * (val - mid) / (high - mid)
    if val >= low:
        return 0.6 * (val - low) / (mid - low)
    return 0.0


def _score_bool(val) -> float:
    """布尔信号: True->1.0, False/None/falsy->0.0"""
    return 1.0 if val else 0.0


def _score_ratio_inverse(ratio: float, good: float = 0.0, bad: float = 0.5) -> float:
    """反向比例: good->1.0, bad->0.0, 线性插值"""
    if ratio <= good:
        return 1.0
    if ratio >= bad:
        return 0.0
    return 1.0 - (ratio - good) / (bad - good)


def _score_paragraph_depth(count: int, mode: str) -> float:
    """段落深度阶梯评分"""
    if mode == "rss":
        if count >= 5:
            return 1.0
        if count >= 3:
            return 0.7
        if count >= 1:
            return 0.3
        return 0.0
    else:  # web
        if count >= 10:
            return 1.0
        if count >= 5:
            return 0.6
        if count >= 3:
            return 0.3
        return 0.0


def _score_field_group_coverage(details: dict, kind: str) -> float:
    """API 字段组覆盖率: matched_groups / total_groups"""
    api_fields = [f.lower() for f in details.get("first_item_fields", [])]
    if not api_fields:
        return 0.0
    patterns = KIND_FIELD_PATTERNS.get(kind, {})
    total = len(patterns)
    if total == 0:
        return 0.0
    matched = sum(
        1 for group_keys in patterns.values()
        if any(f in api_fields for f in group_keys)
    )
    return matched / total


def _compute_signal(sig: tuple, details: dict, kind: str) -> float:
    """根据信号配置计算单个信号得分"""
    name, _weight, scorer, params = sig

    if scorer == "length":
        key = params.get("key", "first_item_chars")
        if key == "first_item_chars":
            val = details.get("first_item_chars", details.get("char_count", 0))
        else:
            val = details.get(key, 0)
        return _score_length(float(val), params["low"], params["mid"], params["high"])

    if scorer == "bool":
        return _score_bool(details.get(params["key"]))

    if scorer == "bool_inverse":
        return _score_bool(not details.get(params["key"]))

    if scorer == "ratio_inverse":
        return _score_ratio_inverse(
            details.get(params["key"], 0.0),
            params.get("good", 0.0),
            params.get("bad", 0.5),
        )

    if scorer == "paragraph_depth":
        return _score_paragraph_depth(
            details.get("paragraph_count", 0),
            params.get("mode", "rss"),
        )

    if scorer == "field_group_coverage":
        return _score_field_group_coverage(details, params.get("kind", kind))

    if scorer == "item_count_ratio":
        return min(details.get("item_count", 0) / 5, 1.0)

    return 0.0


def _derive_grade(score: float) -> str:
    """从质量评分派生等级"""
    a, b, c = GRADE_THRESHOLDS
    if score >= a:
        return "A"
    if score >= b:
        return "B"
    if score >= c:
        return "C"
    return "D"


def evaluate_content_completeness(result: dict, kind: str = "news") -> dict:
    """
    Gate + Quality 双层评估。

    Layer 1 (Gate): 下游 NOT NULL 字段的探针端映射，ALL must pass。
    Layer 2 (Quality): 加权信号评分，仅 gate 全过时计算。

    返回 {complete, score, threshold, gates_passed, gates, signals, grade}。
    """
    empty = {
        "complete": False, "score": 0.0, "threshold": 0.0,
        "gates_passed": False, "gates": {}, "signals": {}, "grade": "D",
    }
    if not result.get("success"):
        return empty

    mode = result["mode"]
    config = KIND_MODE_CONFIG.get(kind, {}).get(mode)
    if config is None:
        return empty

    d = result.get("details", {})

    # Layer 1: Gate 评估
    gates_result = {}
    for gate in config.get("gates", []):
        gates_result[gate[0]] = _evaluate_gate(gate, d, kind)
    gates_passed = all(gates_result.values()) if gates_result else False

    if not gates_passed:
        return {**empty, "gates": gates_result, "gates_passed": False}

    # Layer 2: Quality 评分（仅 gate 全过时）
    scored: dict[str, float] = {}
    total_weight = 0.0
    weighted_sum = 0.0
    for sig in config.get("signals", []):
        name, weight, _scorer, _params = sig
        raw = _compute_signal(sig, d, kind)
        scored[name] = round(raw, 2)
        weighted_sum += weight * raw
        total_weight += weight

    score = weighted_sum / total_weight if total_weight > 0 else 0.0
    threshold = config["threshold"]
    grade = _derive_grade(score)

    return {
        "complete": score >= threshold,
        "score": round(score, 3),
        "threshold": threshold,
        "gates_passed": True,
        "gates": gates_result,
        "signals": scored,
        "grade": grade,
    }


# Section 6: 字段推断
# ═══════════════════════════════════════════════════════════════

def infer_rss_fields(root: ElementTree.Element, items: list) -> dict:
    """从 RSS 探测结果推断爬虫配置字段（保守默认：需 web fallback）"""
    root_tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag
    rss_format = detect_rss_format(root_tag)

    return {
        "crawl_method": "requests",
        "crawl_mode": "feed_only",
        "crawl_use_proxy": 0,
        "crawl_range": "latest",
        "login_requires": 0,
        "scope": "broadcast",
        "trigger": "scheduled",
        "_rss_format": rss_format,
        "_has_full_content": False,
        "_needs_web_fallback": True,
    }


def infer_api_fields(url: str, code: int, data: object, headers_used: dict[str, str] | None) -> dict:
    """从 API 探测结果推断爬虫配置字段"""
    domain = urlparse(url).netloc

    provider_map = {
        "sec.gov": "SEC", "eodhd.com": "EODHD", "polygon.io": "Polygon",
        "alphavantage.co": "Alpha Vantage", "tradingeconomics.com": "TradingEconomics",
        "worldbank.org": "World Bank", "bls.gov": "BLS", "fred.stlouisfed.org": "FRED",
        "finnhub.io": "Finnhub", "cls.cn": "CLS",
    }
    provider = None
    for d, p in provider_map.items():
        if d in domain:
            provider = p
            break
    if not provider:
        parts = domain.replace("www.", "").split(".")
        provider = parts[0] if parts else domain

    auth_method = "none"
    if headers_used:
        if any("authorization" in k.lower() for k in headers_used):
            auth_val = next((v for k, v in headers_used.items() if "authorization" in k.lower()), "")
            if "bearer" in auth_val.lower():
                auth_method = "bearer"
            elif "basic" in auth_val.lower():
                auth_method = "basic"
            else:
                auth_method = "api_key"
        elif any("apikey" in k.lower() or "api-key" in k.lower() for k in headers_used):
            auth_method = "api_key"
    if "apikey=" in url.lower() or "api_key=" in url.lower() or "token=" in url.lower():
        auth_method = "api_key"

    return {
        "api_provider": provider,
        "api_auth_method": auth_method,
        "api_key_ref": f"{provider.lower().replace(' ', '_')}_api_key" if auth_method != "none" else None,
        "login_requires": 0,
        "scope": "broadcast",
        "trigger": "scheduled",
    }


def infer_web_fields(
    body_text: str,
    url: str,
    selector: str | None,
    *,
    anti_crawl: str | None,
    is_spa: str | None,
    paywall: str | None,
    cms: str | None,
) -> dict:
    """
    从 WEB 探测结果推断爬虫配置字段。

    检测结果由调用者（probe_web）传入，避免重复计算。
    """
    fields: dict = {
        "crawl_method": "requests",
        "crawl_mode": "list_detail",
        "crawl_content_selector": selector,
        "crawl_list_selector": None,
        "crawl_json_path": None,
        "crawl_exclude_selector": None,
        "crawl_use_proxy": 0,
        "crawl_range": "latest",
        "max_pages": 10,
        "login_requires": 0,
        "login_method": None,
        "cookie_user_agent": None,
        "cookie_headers": None,
        "cookie_refresh_method": "manual",
        "scope": "broadcast",
        "trigger": "scheduled",
    }

    # 失败场景：反爬或 SPA
    if anti_crawl:
        fields["crawl_method"] = "playwright"
        fields["crawl_use_proxy"] = 1
        return fields
    if is_spa:
        fields["crawl_method"] = "playwright"
        return fields

    # 成功场景：CMS / JSON 路径 / 文章链接
    if cms:
        fields["_cms"] = cms

    nextjs_path = detect_nextjs_json_path(body_text)
    if nextjs_path:
        fields["crawl_json_path"] = nextjs_path

    jsonld_len = detect_jsonld_article(body_text)
    if jsonld_len and not nextjs_path:
        fields["crawl_json_path"] = "articleBody"
        fields["_jsonld_body_chars"] = jsonld_len

    article_count = count_article_links(body_text, url)
    if article_count > 0:
        fields["_article_link_count"] = article_count

    if paywall:
        fields["_paywall"] = paywall

    return fields


# ═══════════════════════════════════════════════════════════════
# Section 7: 探测函数
# ═══════════════════════════════════════════════════════════════

def probe_rss(url: str, output_dir: str = "/tmp") -> dict:
    """RSS/Atom 探测"""
    r = fetch_url(url)
    body_text = r.body.decode("utf-8", errors="replace")

    result: dict = {
        "mode": "rss",
        "url": url,
        "http_code": r.status,
        "content_type": r.content_type,
        "size_bytes": r.size,
    }

    # 连接失败
    if r.error and r.status == 0:
        result.update(success=False, diagnosis=f"连接失败({r.error_type})", grade="D",
                      note=f"连接失败: {r.error}")
        return result

    if r.status != 200:
        anti = detect_anti_crawl(body_text, r.status)
        result.update(success=False, diagnosis=anti or f"HTTP {r.status}", grade="D",
                      note=f"{anti or f'HTTP {r.status}'}")
        return result

    # 检测是否是 XML
    if not _is_xml_response(r.content_type, body_text[:500]):
        result.update(success=False, diagnosis="非XML响应", grade="D",
                      note=f"响应非XML（Content-Type: {r.content_type}）")
        return result

    # 解析 RSS/Atom
    try:
        root = ElementTree.fromstring(r.body)
    except ElementTree.ParseError:
        result.update(success=False, diagnosis="XML解析失败", grade="D",
                      note="XML解析失败（格式错误）")
        return result

    # 统计 items
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    items = root.findall(".//item")  # RSS 2.0
    if not items:
        items = root.findall(".//atom:entry", ns)  # Atom
    if not items:
        items = root.findall(".//{http://www.w3.org/2005/Atom}entry")  # Atom fallback

    item_count = len(items)
    if item_count == 0:
        result.update(success=False, diagnosis="无item/entry", grade="D",
                      note="RSS可达但无item/entry")
        return result

    # 分析第一个 item 的内容丰富度
    first = items[0]
    has_title = (first.find("title") is not None
                 or first.find("{http://www.w3.org/2005/Atom}title") is not None)
    has_link = (first.find("link") is not None
                or first.find("{http://www.w3.org/2005/Atom}link") is not None)

    # 检查正文内容
    content_encoded = first.find("{http://purl.org/rss/1.0/modules/content/}encoded")
    description = first.find("description")
    atom_content = first.find("{http://www.w3.org/2005/Atom}content")

    has_full_content = (content_encoded is not None
                        and content_encoded.text
                        and len(content_encoded.text) > FULL_CONTENT_CHARS)
    has_description = (description is not None
                       and description.text
                       and len(description.text) > RSS_DESCRIPTION_MIN)

    if has_full_content:
        char_count = count_text_chars(content_encoded.text)
        content_desc = f"content:encoded含完整正文~{char_count}字"
    elif has_description:
        char_count = count_text_chars(description.text)
        content_desc = f"description摘要~{char_count}字"
    elif atom_content is not None and atom_content.text:
        char_count = count_text_chars(atom_content.text)
        content_desc = f"Atom content~{char_count}字"
    else:
        char_count = 0
        content_desc = "仅title+link"

    # ── 新增信号采集 ──
    if has_full_content:
        content_source = "content_encoded"
    elif has_description:
        content_source = "description"
    elif atom_content is not None and atom_content.text:
        content_source = "atom_content"
    else:
        content_source = "none"

    _content_raw = ""
    if content_encoded is not None and content_encoded.text:
        _content_raw = content_encoded.text
    elif description is not None and description.text:
        _content_raw = description.text
    elif atom_content is not None and atom_content.text:
        _content_raw = atom_content.text
    paragraph_count = len(re.findall(r"<p[^>]*>", _content_raw, re.IGNORECASE))

    _TRUNCATION_RE = re.compile(
        r"\u2026|\.{3}|\[\u2026\]|Read more|Continue reading|See more|Full story",
        re.IGNORECASE,
    )
    truncation_marker = bool(_TRUNCATION_RE.search(_content_raw))

    # has_content_tag: RSS source provides full-text tag (content:encoded or atom:content)
    has_content_tag = (
        (content_encoded is not None and content_encoded.text and len(content_encoded.text.strip()) > 0)
        or (atom_content is not None and atom_content.text and len(atom_content.text.strip()) > 0)
    )

    # item_consistency: CV of first 5 items' description lengths
    item_consistency = 0.0
    _desc_lens: list[int] = []
    for _itm in items[:5]:
        _desc_el = _itm.find("description")
        if _desc_el is not None and _desc_el.text:
            _desc_lens.append(len(_desc_el.text))
    if len(_desc_lens) >= 2:
        _mean = sum(_desc_lens) / len(_desc_lens)
        if _mean > 0:
            _var = sum((x - _mean) ** 2 for x in _desc_lens) / len(_desc_lens)
            item_consistency = round((_var ** 0.5) / _mean, 3)

    has_pubdate = (first.find("pubDate") is not None
                   or first.find("{http://www.w3.org/2005/Atom}published") is not None)
    grade = ""  # 由 evaluate_content_completeness 统一派生

    # 通过判断：至少有 title + link
    passed = has_title and has_link
    diagnosis = "通过" if passed else "内容不足"
    note = f"{item_count}项含{content_desc}；{grade}级"

    # 保存样本
    sample_path = os.path.join(output_dir, "probe_rss_sample.xml")
    with open(sample_path, "wb") as f:
        f.write(r.body)

    # 字段推断
    fields = infer_rss_fields(root, items)

    result.update(
        success=passed,
        diagnosis=diagnosis,
        grade=grade,
        sample_path=sample_path,
        note=note,
        details={
            "item_count": item_count,
            "has_title": has_title,
            "has_link": has_link,
            "has_full_content": bool(has_full_content),
            "has_content_tag": has_content_tag,
            "has_description": bool(has_description),
            "first_item_chars": char_count,
            "has_pubdate": has_pubdate,
            "content_source": content_source,
            "paragraph_count": paragraph_count,
            "truncation_marker": truncation_marker,
            "item_consistency": item_consistency,
        },
        fields=fields,
    )
    return result


def probe_api(url: str, headers: dict[str, str] | None = None, output_dir: str = "/tmp") -> dict:
    """API JSON 探测"""
    api_headers = {"Accept": "application/json"}
    if headers:
        api_headers.update(headers)

    r = fetch_url(url, api_headers)
    body_text = r.body.decode("utf-8", errors="replace")

    result: dict = {
        "mode": "api",
        "url": url,
        "http_code": r.status,
        "content_type": r.content_type,
        "size_bytes": r.size,
    }

    if r.error and r.status == 0:
        result.update(success=False, diagnosis=f"连接失败({r.error_type})", grade="D",
                      note=f"连接失败: {r.error}")
        return result

    if r.status != 200:
        anti = detect_anti_crawl(body_text, r.status)
        result.update(success=False, diagnosis=anti or f"HTTP {r.status}", grade="D",
                      note=f"{anti or f'HTTP {r.status}'}")
        return result

    # 解析 JSON
    try:
        data = json.loads(body_text)
    except json.JSONDecodeError:
        result.update(success=False, diagnosis="非JSON响应", grade="D",
                      note=f"响应非JSON（Content-Type: {r.content_type}）")
        return result

    # 分析数据结构
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = None
        for key in ["data", "results", "items", "articles", "news", "records",
                     "entries", "telegraphList", "list"]:
            if key in data and isinstance(data[key], list):
                items = data[key]
                break
        if items is None:
            items = [data]
    else:
        result.update(success=False, diagnosis="未知JSON结构", grade="D",
                      note="JSON结构无法识别")
        return result

    item_count = len(items)
    if item_count == 0:
        result.update(success=False, diagnosis="空数据数组", grade="D",
                      note="API可达但返回空数组")
        return result

    # 分析第一条数据
    first = items[0] if isinstance(items[0], dict) else {}
    field_count = len(first)
    _CONTENT_KEYS = ["content", "body", "text", "article", "description", "summary", "abstract"]
    has_content_field = any(k in first for k in _CONTENT_KEYS)
    max_field_len = max((len(str(v)) for v in first.values()), default=0) if first else 0

    if has_content_field:
        content_key = next(k for k in _CONTENT_KEYS if k in first)
        char_count = len(str(first[content_key]))
        content_desc = f"{content_key}字段~{char_count}字"
    else:
        char_count = max_field_len
        content_desc = f"{field_count}个字段，最长值{max_field_len}字"


    # ── 新增信号采集 ──
    _DATE_RE = re.compile(r'date|time|created|published|updated', re.IGNORECASE)
    has_date_field = any(
        _DATE_RE.search(k) and first.get(k) not in (None, '', [], {})
        for k in first
    )
    has_numeric_field = any(isinstance(v, (int, float)) for v in first.values())
    _non_empty = [v for v in first.values() if v not in (None, '', [], {})]
    empty_field_ratio = round(1 - len(_non_empty) / max(field_count, 1), 3)

    grade = ""  # 由 evaluate_content_completeness 统一派生

    passed = item_count > 0 and (has_content_field or field_count >= API_CONTENT_MIN_FIELDS)
    note = f"JSON {item_count}条；{content_desc}；{grade}级"

    # 保存样本
    sample_path = os.path.join(output_dir, "probe_api_sample.json")
    with open(sample_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    # 字段推断
    fields = infer_api_fields(url, r.status, data, api_headers)

    result.update(
        success=passed,
        diagnosis="通过" if passed else "内容不足",
        grade=grade,
        sample_path=sample_path,
        note=note,
        details={
            "item_count": item_count,
            "field_count": field_count,
            "has_content_field": has_content_field,
            "first_item_chars": char_count,
            "first_item_fields": list(first.keys())[:20] if first else [],
            "has_date_field": has_date_field,
            "has_numeric_field": has_numeric_field,
            "empty_field_ratio": empty_field_ratio,
        },
        fields=fields,
    )
    return result


def probe_web(url: str, selector: str | None = None, output_dir: str = "/tmp") -> dict:
    """WEB HTML 探测"""
    r = fetch_url(url)
    body_text = r.body.decode("utf-8", errors="replace")

    result: dict = {
        "mode": "web",
        "url": url,
        "http_code": r.status,
        "content_type": r.content_type,
        "size_bytes": r.size,
    }

    if r.error and r.status == 0:
        result.update(success=False, diagnosis=f"连接失败({r.error_type})", grade="D",
                      note=f"连接失败: {r.error}")
        return result

    if r.status != 200:
        anti = detect_anti_crawl(body_text, r.status)
        result.update(success=False, diagnosis=anti or f"HTTP {r.status}", grade="D",
                      note=f"{anti or f'HTTP {r.status}'}")
        return result

    # 前置检测（结果传递给 infer_web_fields，避免重复计算）
    anti = detect_anti_crawl(body_text, r.status)
    spa = detect_spa(body_text, r.size)
    paywall = detect_paywall(body_text)
    cms = detect_cms(body_text)

    if anti:
        fields = infer_web_fields(body_text, url, selector,
                                  anti_crawl=anti, is_spa=None, paywall=None, cms=None)
        result.update(success=False, diagnosis=anti, grade="D", note=anti, fields=fields)
        return result

    if spa:
        fields = infer_web_fields(body_text, url, selector,
                                  anti_crawl=None, is_spa=spa, paywall=None, cms=None)
        result.update(success=False, diagnosis=spa, grade="D", note=spa, fields=fields)
        return result

    # 文本提取
    char_count = 0
    extraction_method = "readability估算"

    if selector:
        extracted = _regex_extract(body_text, selector)
        if extracted:
            char_count = count_text_chars(extracted)
            extraction_method = f"CSS `{selector}`"

    # Paragraph-level signals (always computed for scoring)
    _all_paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", body_text, re.DOTALL | re.IGNORECASE)
    paragraph_count = len(_all_paragraphs)

    if char_count == 0:
        all_text = " ".join(_all_paragraphs)
        char_count = count_text_chars(all_text)
        extraction_method = f"<p>标签提取({paragraph_count}段)"

    # 检测标题和元数据
    # ── 新增信号采集 ──
    content_to_page_ratio = round(char_count / max(r.size, 1), 4)
    _TRUNC_WEB_RE = re.compile(
        r"Read more|Subscribe to|Sign in to|Continue reading|…|\.{3}",
        re.IGNORECASE,
    )
    truncation_marker = bool(_TRUNC_WEB_RE.search(body_text[:50000]))
    has_structured_data = bool(
        re.search(r"<table[\s>]|application/ld\+json|__NEXT_DATA__", body_text[:50000], re.IGNORECASE)
    )

    has_title = bool(re.search(r"<h1[^>]*>", body_text, re.IGNORECASE))
    has_time = bool(re.search(
        r'<time|datetime=|pubdate|published|"datePublished"', body_text, re.IGNORECASE
    ))
    has_author = bool(re.search(
        r'"author"|class=".*author.*"|rel="author"', body_text, re.IGNORECASE
    ))

    grade = ""  # 由 evaluate_content_completeness 统一派生

    # 通过判断
    passed = char_count >= MIN_BODY_CHARS_PASS and not (paywall and char_count < FULL_CONTENT_CHARS)

    if passed:
        diagnosis = "通过"
        note_parts = ["SSR可达", f"{extraction_method}~{char_count}字"]
        if paywall:
            note_parts.append("部分paywall")
    else:
        if paywall:
            diagnosis = paywall
        elif char_count < MIN_BODY_CHARS_PASS:
            diagnosis = f"正文不足（{char_count}字）"
        else:
            diagnosis = "未知问题"
        note_parts = [diagnosis]

    note_parts.append(f"{grade}级")
    note = "；".join(note_parts)

    # 保存样本
    sample_path = os.path.join(output_dir, "probe_web_sample.html")
    with open(sample_path, "wb") as f:
        f.write(r.body)

    # 字段推断（传入已计算的检测结果）
    fields = infer_web_fields(body_text, url, selector,
                              anti_crawl=anti, is_spa=spa, paywall=paywall, cms=cms)

    result.update(
        success=passed,
        diagnosis=diagnosis,
        grade=grade,
        sample_path=sample_path,
        note=note,
        details={
            "char_count": char_count,
            "extraction_method": extraction_method,
            "has_title": has_title,
            "has_time": has_time,
            "has_author": has_author,
            "paywall": paywall,
            "cms": cms,
            "page_size_kb": round(r.size / 1024, 1),
            "paragraph_count": paragraph_count,
            "content_to_page_ratio": content_to_page_ratio,
            "truncation_marker": truncation_marker,
            "has_structured_data": has_structured_data,
        },
        fields=fields,
    )
    return result


# ═══════════════════════════════════════════════════════════════
# Section 8: URL 发现
# ═══════════════════════════════════════════════════════════════

def discover_html_links(body_text: str, base_url: str) -> list[dict]:
    """从 HTML 解析 <link rel="alternate"> RSS/Atom 链接"""
    links = []
    for match in re.finditer(r'<link[^>]+rel=["\']alternate["\'][^>]*>', body_text, re.IGNORECASE):
        tag = match.group(0)
        type_m = re.search(r'type=["\']([^"\']+)["\']', tag)
        href_m = re.search(r'href=["\']([^"\']+)["\']', tag)
        title_m = re.search(r'title=["\']([^"\']+)["\']', tag)
        if not href_m or not type_m:
            continue
        link_type = type_m.group(1).lower()
        if not any(x in link_type for x in ["rss", "atom", "xml"]):
            continue
        href = href_m.group(1)
        if href.startswith("/"):
            parsed = urlparse(base_url)
            href = f"{parsed.scheme}://{parsed.netloc}{href}"
        elif not href.startswith("http"):
            href = base_url.rstrip("/") + "/" + href
        links.append({
            "url": href,
            "title": title_m.group(1) if title_m else None,
            "link_type": link_type,
        })
    return links


def check_sitemap(base_url: str) -> dict | None:
    """检查 sitemap.xml 获取内容结构信息"""
    sitemap_url = base_url.rstrip("/") + "/sitemap.xml"
    r = fetch_url(sitemap_url, timeout=SITEMAP_TIMEOUT, retries=0)
    if r.status != 200 or r.size == 0:
        return None
    body_text = r.body.decode("utf-8", errors="replace")
    locs = re.findall(r'<loc>(.*?)</loc>', body_text)
    article_count = sum(1 for u in locs if re.search(r'/article/|/news/|/story/|/\d{4}/', u))
    return {"total_urls": len(locs), "article_urls": article_count}


def _load_rss_paths() -> list[str]:
    """从 source_patterns.json 加载 RSS 路径列表（带 fallback）"""
    patterns = load_patterns()
    return patterns.get("extended_rss_paths", _FALLBACK_RSS_PATHS)


def discover(url: str, output_dir: str = "/tmp", kind: str = "news") -> dict:
    """完整 URL 发现：HTML <link> + CMS 路径 + 扩展探测 + sitemap + domain shortcut

    kind 参数影响正文完整度判定标准（news/filing/calendar/social/metric）。
    """
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"
    domain = parsed.netloc.replace("www.", "")

    patterns = load_patterns()
    candidates: list[dict] = []
    cms = None

    _log("STEP", f"开始发现 {domain} 的所有可用端点...")

    # ── 1. 抓取主页面 ──
    _log("STEP", "抓取主页面...")
    r = fetch_url(url)
    body_text = r.body.decode("utf-8", errors="replace")

    if r.status == 200:
        candidates.append({"url": url, "type": "WEB", "source": "main_page", "confidence": "high"})
        _log("OK", f"主页面可达（{round(r.size / 1024)}KB）")
        cms = detect_cms(body_text)
        if cms:
            _log("INFO", f"CMS: {cms}")
    else:
        _log("FAIL", f"HTTP {r.status}" + (f" ({r.error_type})" if r.error_type else ""))

    # ── 2. HTML <link> 标签 ──
    _log("STEP", "解析 HTML <link rel='alternate'> 标签...")
    html_links: list[dict] = []
    if r.status == 200:
        html_links = discover_html_links(body_text, base_url)
        for link in html_links:
            candidates.append({
                "url": link["url"], "type": "RSS",
                "source": "html_link", "confidence": "high",
                "title": link.get("title"),
            })
            title_suffix = f" ({link['title']})" if link.get("title") else ""
            _log("OK", f"{link['url']}{title_suffix}")
        if not html_links:
            _log("FAIL", "无 <link> RSS 标签")

    # ── 3. CMS 特定路径 ──
    known_urls = {c["url"] for c in candidates}
    if cms:
        cms_paths = patterns.get("cms_rss_paths", {}).get(cms, [])
        if cms_paths:
            _log("STEP", f"尝试 {cms} 特定路径（{len(cms_paths)} 条）...")
            for path in cms_paths:
                test_url = base_url + path
                if test_url in known_urls:
                    continue
                tr = fetch_url(test_url, timeout=DISCOVERY_TIMEOUT, retries=0)
                bt = tr.body.decode("utf-8", errors="replace")[:500]
                if tr.status == 200 and _is_xml_response(tr.content_type, bt):
                    candidates.append({
                        "url": test_url, "type": "RSS",
                        "source": f"cms_{cms.lower()}", "confidence": "high",
                    })
                    known_urls.add(test_url)
                    _log("OK", f"{path} -> RSS（{tr.size} bytes）")

    # ── 4. 扩展路径探测 ──
    extended = patterns.get("extended_rss_paths", _FALLBACK_RSS_PATHS)
    untested = [p for p in extended if base_url + p not in known_urls]
    _log("STEP", f"扩展路径探测（{len(untested)} 条）...")
    rss_found = 0
    for path in untested:
        test_url = base_url + path
        tr = fetch_url(test_url, timeout=DISCOVERY_TIMEOUT, retries=0)
        bt = tr.body.decode("utf-8", errors="replace")[:500]
        if tr.status == 200 and _is_xml_response(tr.content_type, bt):
            candidates.append({
                "url": test_url, "type": "RSS",
                "source": "path_probe", "confidence": "medium",
            })
            known_urls.add(test_url)
            rss_found += 1
            _log("OK", f"{path} -> RSS（{tr.size} bytes）")
    if rss_found == 0:
        _log("FAIL", "无新 RSS 路径")

    # ── 5. 已知源快捷匹配 ──
    shortcuts = patterns.get("domain_shortcuts", {})
    for name, info in shortcuts.items():
        if name in domain or info.get("domain", "") in domain:
            rss_url = info.get("rss")
            if rss_url:
                full = rss_url if rss_url.startswith("http") else f"https://{rss_url}"
                if full not in known_urls:
                    candidates.append({
                        "url": full, "type": "RSS",
                        "source": "domain_shortcut", "confidence": "high",
                    })
                    known_urls.add(full)
                    _log("OK", f"已知源快捷: {full}")
            if info.get("note"):
                _log("WARN", info["note"])

    # ── 6. Sitemap ──
    _log("STEP", "检查 sitemap.xml...")
    sitemap_info = check_sitemap(base_url)
    if sitemap_info:
        _log("OK", f"{sitemap_info['total_urls']} URL（{sitemap_info['article_urls']} 篇文章模式）")
    else:
        _log("FAIL", "无 sitemap 或不可达")

    # ── 7. 验证 RSS 候选 ──
    rss_cands = [c for c in candidates if c["type"] == "RSS"]
    probe_results: list[dict] = []
    if rss_cands:
        _log("STEP", f"验证 {len(rss_cands)} 个 RSS 候选...")
        for cand in rss_cands[:5]:
            pr = probe_rss(cand["url"], output_dir)
            pr["_source"] = cand["source"]
            probe_results.append(pr)
            s = "OK" if pr["success"] else "FAIL"
            _log(s, f"{cand['url']}")
            if pr["success"]:
                _log("INFO", f"  {pr['note']}")

    # ── 8. 验证 WEB ──
    if r.status == 200:
        _log("STEP", "验证 WEB 主页面...")
        web_r = probe_web(url, None, output_dir)
        web_r["_source"] = "main_page"
        probe_results.append(web_r)
        s = "OK" if web_r["success"] else "FAIL"
        _log(s, f"WEB -> {web_r.get('note', web_r.get('diagnosis', ''))}")

    # ── 汇总：按 kind 评估正文完整度，完整优先，同等下 API > RSS > WEB ──
    passed = [pr for pr in probe_results if pr.get("success")]
    for pr in passed:
        eval_result = evaluate_content_completeness(pr, kind)
        pr["_has_full_content"] = eval_result["complete"]
        pr["_completeness"] = eval_result
        pr["grade"] = eval_result["grade"]
        # 修复字段推断时序：评分后更新 RSS fields
        if pr["mode"] == "rss" and eval_result["complete"] and pr.get("fields"):
            pr["fields"]["_needs_web_fallback"] = False
            pr["fields"]["_has_full_content"] = True
    full_content = [pr for pr in passed if pr["_has_full_content"]]

    def _sort_key(x: dict) -> tuple:
        return (TYPE_PRIORITY.get(x["mode"], 99), GRADE_ORDER.get(x["grade"], 99))

    if full_content:
        # 有正文完整的候选：按 type 优先级 + grade 排序
        best = sorted(full_content, key=_sort_key)[0]
        content_status = "full_content"
    elif passed:
        # 无正文完整但有可达候选：仍选最优，标记 partial
        best = sorted(passed, key=_sort_key)[0]
        content_status = "partial_content"
    else:
        best = None
        content_status = "none"

    _log("STEP", "=" * 50)
    _log("INFO", f"kind={kind}, 候选: {len(candidates)}, 通过: {len(passed)}, 正文完整: {len(full_content)}")
    if best and content_status == "full_content":
        _log("OK", f"推荐: {best['mode'].upper()} -> {best['url']} ({best['grade']}级, {kind}完整)")
    elif best:
        _log("WARN", f"最优: {best['mode'].upper()} -> {best['url']} ({best['grade']}级, {kind}不完整)")
    else:
        _log("FAIL", "无通过验证的端点")
    if cms:
        _log("INFO", f"CMS: {cms}")

    return {
        "mode": "discover",
        "kind": kind,
        "domain": domain,
        "base_url": base_url,
        "cms": cms,
        "sitemap": sitemap_info,
        "candidates": candidates,
        "probe_results": probe_results,
        "passed_count": len(passed),
        "full_content_count": len(full_content),
        "content_status": content_status,
        "recommended": {
            "type": best["mode"].upper() if best else None,
            "url": best["url"] if best else None,
            "grade": best["grade"] if best else None,
            "note": best.get("note") if best else None,
            "has_full_content": best.get("_has_full_content", False) if best else False,
            "completeness_score": best["_completeness"]["score"] if best and "_completeness" in best else 0.0,
            "completeness_gates": best["_completeness"]["gates"] if best and "_completeness" in best else {},
            "completeness_signals": best["_completeness"]["signals"] if best and "_completeness" in best else {},
            "fields": best.get("fields") if best else None,
        },
    }


# ═══════════════════════════════════════════════════════════════
# Section 9: 历史记录 & 模式注册表 I/O
# ═══════════════════════════════════════════════════════════════

def load_patterns() -> dict:
    """加载模式注册表（容错：文件不存在返回空 dict）"""
    if PATTERNS_FILE.exists():
        try:
            with open(PATTERNS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def append_probe_history(result: dict, source_name: str | None = None) -> None:
    """追加探测结果到历史日志（JSONL 格式）"""
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "name": source_name,
        "url": result.get("url"),
        "mode": result.get("mode"),
        "success": result.get("success"),
        "http_code": result.get("http_code"),
        "grade": result.get("grade"),
        "diagnosis": result.get("diagnosis"),
    }
    if "fields" in result:
        entry["fields"] = {k: v for k, v in result["fields"].items() if not k.startswith("_")}
    try:
        HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


# ═══════════════════════════════════════════════════════════════
# Section 10: 日志 & CLI 入口
# ═══════════════════════════════════════════════════════════════

_LOG_PREFIX = {
    "INFO": "[.]",
    "OK": "[+]",
    "FAIL": "[-]",
    "WARN": "[!]",
    "STEP": "[>]",
}


def _log(level: str, msg: str) -> None:
    """结构化日志输出到 stderr（JSON 输出走 stdout）"""
    ts = datetime.now().strftime("%H:%M:%S")
    prefix = _LOG_PREFIX.get(level, "[?]")
    print(f"{ts} {prefix} {msg}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Feed 数据源多协议探测")
    parser.add_argument(
        "--mode", required=True,
        choices=["rss", "api", "web", "discover", "auto", "rss-discover"],
        help="探测模式（auto/rss-discover 已弃用，等同 discover）",
    )
    parser.add_argument("--url", required=True)
    parser.add_argument("--name", help="数据源名称（用于历史日志）")
    parser.add_argument("--selector", help="WEB 模式的 CSS 选择器")
    parser.add_argument("--headers", help="API 模式的额外请求头（JSON 字符串）")
    parser.add_argument("--output", default="/tmp/probe_result.json", help="输出 JSON 路径")
    parser.add_argument(
        "--kind", default="news",
        choices=["news", "filing", "calendar", "social", "metric"],
        help="数据源 kind（影响正文完整度判定标准，默认 news）",
    )
    parser.add_argument("--no-history", action="store_true", help="不记录到探测历史")
    args = parser.parse_args()

    # 弃用模式别名
    mode = args.mode
    if mode in ("auto", "rss-discover"):
        _log("WARN", f"--mode {mode} 已弃用，自动切换为 discover")
        mode = "discover"

    headers = json.loads(args.headers) if args.headers else None
    output_dir = os.path.dirname(args.output) or "/tmp"

    if mode == "rss":
        result = probe_rss(args.url, output_dir)
    elif mode == "api":
        result = probe_api(args.url, headers, output_dir)
    elif mode == "web":
        result = probe_web(args.url, args.selector, output_dir)
    elif mode == "discover":
        result = discover(args.url, output_dir, kind=args.kind)
    else:
        _log("FAIL", f"未知模式: {mode}")
        sys.exit(1)

    # 单协议探测时补充完整性评估（discover 内部已处理）
    if mode in ("rss", "api", "web"):
        eval_r = evaluate_content_completeness(result, args.kind)
        result["grade"] = eval_r["grade"]
        result["_completeness"] = eval_r

    # 记录探测历史
    if not args.no_history:
        if mode == "discover":
            for pr in result.get("probe_results", []):
                append_probe_history(pr, args.name)
        else:
            append_probe_history(result, args.name)

    # 输出 JSON 到文件
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    _log("INFO", f"结果已保存: {args.output}")

    # JSON 输出到 stdout（可被管道捕获）
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()