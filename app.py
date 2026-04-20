from __future__ import annotations

import json
import math
import mimetypes
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
DATA_DIR = ROOT_DIR / "data"
STATE_PATH = DATA_DIR / "manual_state.json"

DEFAULT_STREAMER_ID = "kyaang123"
DEFAULT_DISPLAY_NAME = "캬앙"
DEFAULT_TIER = "best"
DEFAULT_POLICY_DATE = date(2026, 6, 1)
VIEW_COUNT_CHANGE_AT = datetime(2025, 1, 14, 11, 35, 0)
DEFAULT_POLL_INTERVAL_SECONDS = 60
DEFAULT_PAGE_TITLE = "{display_name} 다시보기 백업"
DEFAULT_PAGE_HEADING = "{display_name} 다시보기 살리기 운동"
MAX_FETCH_WORKERS = 8
MAX_COMMENT_SCAN_WORKERS = 4
COMMENT_ROWS_PER_PAGE = 100
REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36"
}


@dataclass(frozen=True)
class MonitorSettings:
    streamer_id: str
    display_name: str
    page_title: str
    page_heading: str
    streamer_tier: str
    policy_date: date
    poll_interval_seconds: int
    host: str
    port: int


def normalize_tier(value: str) -> str:
    tier = (value or DEFAULT_TIER).strip().lower()
    if tier not in {"general", "best", "partner"}:
        return DEFAULT_TIER
    return tier


def parse_policy_date(value: str) -> date:
    if not value:
        return DEFAULT_POLICY_DATE
    normalized = str(value).strip()
    if len(normalized) >= 10:
        normalized = normalized[:10]
    return datetime.strptime(normalized, "%Y-%m-%d").date()


def format_display_text(value: Optional[str], display_name: str, default_template: str) -> str:
    template = (value or default_template).strip() or default_template
    return template.replace("{display_name}", display_name)


def add_years(source_date: date, years: int) -> date:
    try:
        return source_date.replace(year=source_date.year + years)
    except ValueError:
        # Handles Feb 29 -> Feb 28.
        return source_date.replace(year=source_date.year + years, month=2, day=28)


def parse_reg_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")


def format_duration(milliseconds: int) -> str:
    total_seconds = max(0, milliseconds // 1000)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:d}:{seconds:02d}"


def normalize_url(value: str) -> str:
    if not value:
        return ""
    if value.startswith("//"):
        return f"https:{value}"
    return value


def load_state_cache(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"comment_checks": {}}
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        return {"comment_checks": {}}
    comment_checks = data.get("comment_checks")
    if not isinstance(comment_checks, dict):
        comment_checks = {}
    return {
        "comment_checks": {
            str(title_no): record for title_no, record in comment_checks.items() if isinstance(record, dict)
        }
    }


def save_state_cache(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
    temp_path.replace(path)


def extract_auto_delete_lookup(entries: Iterable[Any]) -> Dict[str, set[str]]:
    ids: set[str] = set()
    titles: set[str] = set()
    for entry in entries or []:
        if isinstance(entry, dict):
            for key in ("title_no", "titleNo", "id", "no"):
                value = entry.get(key)
                if value is not None:
                    ids.add(str(value))
            for key in ("title_name", "title", "name"):
                value = entry.get(key)
                if isinstance(value, str):
                    titles.add(value.strip())
        elif entry is not None:
            ids.add(str(entry))
    return {"ids": ids, "titles": titles}


def safe_int(value: Any) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def summarize_comment(value: Any, limit: int = 80) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1].rstrip()}…"


def iter_json_objects(payload: Any) -> Iterable[Dict[str, Any]]:
    stack = [payload]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)


def extract_support_evidence(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    for node in iter_json_objects(payload):
        starballoon_cnt = safe_int(node.get("starballoon_cnt"))
        gift_cnt = safe_int(node.get("gift_cnt"))
        if starballoon_cnt < 10 and gift_cnt < 10:
            continue

        if starballoon_cnt >= gift_cnt:
            kind = "starballoon"
            amount = starballoon_cnt
        else:
            kind = "adballoon"
            amount = gift_cnt

        return {
            "supported": True,
            "kind": kind,
            "amount": amount,
            "comment_no": str(node.get("p_comment_no") or node.get("comment_no") or ""),
            "comment_preview": summarize_comment(node.get("comment")),
            "user_id": str(node.get("user_id") or ""),
            "user_nick": str(node.get("user_nick") or ""),
            "reg_date": str(node.get("reg_date") or ""),
        }
    return None


def classify_vod(
    raw_vod: Dict[str, Any],
    streamer_tier: str,
    policy_date: date,
    auto_delete_lookup: Dict[str, set[str]],
    comment_check: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    title_no = str(raw_vod["title_no"])
    uploaded_at = parse_reg_date(raw_vod["reg_date"])
    uploaded_date = uploaded_at.date()
    counts = raw_vod.get("count", {})
    ucc = raw_vod.get("ucc", {})
    raw_display_views = counts.get("read_cnt")
    display_views = safe_int(raw_display_views if raw_display_views not in (None, "") else counts.get("vod_read_cnt"))
    raw_pure_views = counts.get("vod_read_cnt")
    pure_views = safe_int(display_views if raw_pure_views in (None, "") else raw_pure_views)
    comment_count = safe_int(counts.get("comment_cnt"))
    merged_view_count_applies = uploaded_at >= VIEW_COUNT_CHANGE_AT
    if not merged_view_count_applies:
        pure_views = display_views
    estimated_live_views = max(display_views - pure_views, 0) if merged_view_count_applies else 0
    comment_check = comment_check or {}
    auto_support_confirmed = bool(comment_check.get("supported"))
    support_confirmation_mode = "auto" if auto_support_confirmed else "none"
    support_confirmed = auto_support_confirmed

    future_permanent = False
    future_expiry: Optional[date] = None
    future_reason = ""

    if streamer_tier == "partner":
        future_permanent = True
        future_reason = "partner_permanent"
    elif support_confirmed:
        future_permanent = True
        future_reason = "pre_policy_support_confirmed"
    elif streamer_tier == "best":
        if pure_views > 1000:
            future_permanent = True
            future_reason = "best_views_over_1000"
        else:
            future_expiry = add_years(uploaded_date, 2)
            future_reason = "best_basic_2_years"
    else:
        if pure_views >= 50:
            future_expiry = add_years(uploaded_date, 1)
            future_reason = "general_views_50_plus_1_year"
        else:
            future_expiry = uploaded_date + timedelta(days=90)
            future_reason = "general_basic_90_days"

    current_permanent = False
    current_reason = ""
    current_expiry: Optional[date] = None

    if streamer_tier in {"best", "partner"}:
        current_permanent = True
        current_reason = f"{streamer_tier}_current_permanent"
    elif support_confirmed:
        current_permanent = True
        current_reason = "current_support_confirmed"
    elif pure_views >= 50:
        current_permanent = True
        current_reason = "current_views_50_plus"
    else:
        current_expiry = uploaded_date + timedelta(days=90)
        current_reason = "current_basic_90_days"

    delete_on_policy_day = bool(future_expiry and future_expiry <= policy_date)
    needs_pre_policy_support = not future_permanent and streamer_tier != "partner" and not support_confirmed
    expires_after_policy = bool(future_expiry and future_expiry > policy_date)

    if future_permanent:
        urgency = "safe"
    elif delete_on_policy_day:
        urgency = "policy_day"
    elif future_expiry and future_expiry <= policy_date + timedelta(days=90):
        urgency = "soon"
    else:
        urgency = "later"

    title_name = raw_vod.get("title_name", "")
    api_auto_delete_flag = title_no in auto_delete_lookup["ids"] or title_name in auto_delete_lookup["titles"]

    thumbnail = normalize_url(ucc.get("thumb", ""))
    return {
        "title_no": title_no,
        "title_name": title_name,
        "player_url": f"https://vod.sooplive.com/player/{title_no}",
        "thumbnail_url": thumbnail,
        "uploaded_at": uploaded_at.isoformat(),
        "uploaded_date": uploaded_date.isoformat(),
        "duration_label": format_duration(int(ucc.get("total_file_duration") or 0)),
        "display_views": display_views,
        "pure_views": pure_views,
        "estimated_live_views": estimated_live_views,
        "merged_view_count_applies": merged_view_count_applies,
        "view_count_changed_at": VIEW_COUNT_CHANGE_AT.isoformat(),
        "comment_count": comment_count,
        "like_count": int(counts.get("like_cnt") or 0),
        "current_permanent": current_permanent,
        "current_expiry_date": current_expiry.isoformat() if current_expiry else None,
        "current_reason": current_reason,
        "future_permanent": future_permanent,
        "future_expiry_date": future_expiry.isoformat() if future_expiry else None,
        "future_reason": future_reason,
        "delete_on_policy_day": delete_on_policy_day,
        "expires_after_policy": expires_after_policy,
        "needs_pre_policy_support": needs_pre_policy_support,
        "urgency": urgency,
        "support_confirmed": support_confirmed,
        "support_confirmation_mode": support_confirmation_mode,
        "auto_support_confirmed": auto_support_confirmed,
        "auto_support_kind": comment_check.get("kind"),
        "auto_support_amount": safe_int(comment_check.get("amount")),
        "auto_support_comment_no": comment_check.get("comment_no") or None,
        "auto_support_comment_preview": comment_check.get("comment_preview") or "",
        "auto_support_user_id": comment_check.get("user_id") or "",
        "auto_support_user_nick": comment_check.get("user_nick") or "",
        "auto_support_reg_date": comment_check.get("reg_date") or None,
        "auto_support_checked_at": comment_check.get("checked_at"),
        "api_auto_delete_flag": api_auto_delete_flag,
        "raw_file_type": ucc.get("file_type"),
        "raw_grade": ucc.get("grade"),
        "views_900_plus": pure_views >= 900,
        "views_1000_plus": pure_views > 1000,
    }


class SoopReplayMonitor:
    def __init__(self, settings: MonitorSettings) -> None:
        self.settings = settings
        self._lock = threading.Lock()
        self._refresh_lock = threading.Lock()
        self._state_cache = load_state_cache(STATE_PATH)
        self._snapshot: Dict[str, Any] = {
            "streamer_id": settings.streamer_id,
            "display_name": settings.display_name,
            "page_title": settings.page_title,
            "page_heading": settings.page_heading,
            "streamer_tier": settings.streamer_tier,
            "policy_date": settings.policy_date.isoformat(),
            "generated_at": None,
            "refreshing": False,
            "error": None,
            "summary": {},
            "vods": [],
        }
        self._stop_event = threading.Event()
        self._poller = threading.Thread(target=self._poll_forever, name="soop-monitor-poller", daemon=True)

    def start(self) -> None:
        self.refresh_now()
        self._poller.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._poller.is_alive():
            self._poller.join(timeout=2)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._snapshot))

    def trigger_refresh(self) -> None:
        thread = threading.Thread(target=self.refresh_now, name="soop-monitor-manual-refresh", daemon=True)
        thread.start()

    def refresh_now(self) -> None:
        if not self._refresh_lock.acquire(blocking=False):
            return

        try:
            with self._lock:
                comment_checks = json.loads(json.dumps(self._state_cache.get("comment_checks", {})))

            with self._lock:
                self._snapshot["refreshing"] = True
                self._snapshot["error"] = None

            page_one = self._fetch_review_page(1)
            last_page = int(page_one["meta"]["last_page"])
            pages: Dict[int, Dict[str, Any]] = {1: page_one}

            if last_page > 1:
                with ThreadPoolExecutor(max_workers=MAX_FETCH_WORKERS) as executor:
                    futures = {
                        executor.submit(self._fetch_review_page, page_number): page_number
                        for page_number in range(2, last_page + 1)
                    }
                    for future in as_completed(futures):
                        page_number = futures[future]
                        pages[page_number] = future.result()

            raw_vods: list[Dict[str, Any]] = []
            auto_del_entries: list[Any] = []
            for page_number in range(1, last_page + 1):
                payload = pages[page_number]
                raw_vods.extend(payload.get("data", []))
                auto_del_entries.extend(payload.get("auto_del_vods", []))

            auto_delete_lookup = extract_auto_delete_lookup(auto_del_entries)
            scanned_comment_checks = dict(comment_checks)
            comment_scan_candidates = [
                item
                for item in raw_vods
                if self._should_scan_comment_support(
                    raw_vod=item,
                    comment_checks=scanned_comment_checks,
                )
            ]

            if comment_scan_candidates:
                with ThreadPoolExecutor(max_workers=MAX_COMMENT_SCAN_WORKERS) as executor:
                    futures = {
                        executor.submit(self._scan_comment_support, item): str(item["title_no"])
                        for item in comment_scan_candidates
                    }
                    for future in as_completed(futures):
                        result = future.result()
                        scanned_comment_checks[result["title_no"]] = result

                with self._lock:
                    self._state_cache["comment_checks"] = {
                        item["title_no"]: item for item in scanned_comment_checks.values() if item
                    }
                    save_state_cache(STATE_PATH, self._state_cache)

            computed_vods = [
                classify_vod(
                    raw_vod=item,
                    streamer_tier=self.settings.streamer_tier,
                    policy_date=self.settings.policy_date,
                    auto_delete_lookup=auto_delete_lookup,
                    comment_check=scanned_comment_checks.get(str(item["title_no"])),
                )
                for item in raw_vods
            ]
            computed_vods.sort(
                key=lambda item: (
                    {"policy_day": 0, "soon": 1, "later": 2, "safe": 3}[item["urgency"]],
                    item["future_expiry_date"] or "9999-12-31",
                    item["uploaded_at"],
                )
            )

            summary = self._build_summary(computed_vods)

            with self._lock:
                self._snapshot = {
                    "streamer_id": self.settings.streamer_id,
                    "display_name": self.settings.display_name,
                    "page_title": self.settings.page_title,
                    "page_heading": self.settings.page_heading,
                    "streamer_tier": self.settings.streamer_tier,
                    "policy_date": self.settings.policy_date.isoformat(),
                    "generated_at": datetime.now(timezone.utc).isoformat(),
                    "refreshing": False,
                    "error": None,
                    "summary": summary,
                    "vods": computed_vods,
                }
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self._snapshot["refreshing"] = False
                self._snapshot["error"] = str(exc)
        finally:
            self._refresh_lock.release()

    def _poll_forever(self) -> None:
        while not self._stop_event.wait(self.settings.poll_interval_seconds):
            self.refresh_now()

    def _fetch_review_page(self, page_number: int) -> Dict[str, Any]:
        url = f"https://bjapi.afreecatv.com/api/{self.settings.streamer_id}/vods/review?page={page_number}"
        request = Request(url, headers=REQUEST_HEADERS)
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f"SOOP API HTTP {exc.code} on page {page_number}") from exc
        except URLError as exc:
            raise RuntimeError(f"SOOP API connection failed on page {page_number}") from exc

    def _fetch_comment_page(self, title_no: str, page_number: int) -> Dict[str, Any]:
        payload = urlencode(
            {
                "nTitleNo": title_no,
                "nPageNo": page_number,
                "nRowsPerPage": COMMENT_ROWS_PER_PAGE,
                "nOrderType": 1,
            }
        ).encode("utf-8")
        headers = {
            **REQUEST_HEADERS,
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": f"https://vod.sooplive.com/player/{title_no}",
        }
        request = Request("https://api.m.sooplive.com/station/comment/a/list", data=payload, headers=headers)
        try:
            with urlopen(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            raise RuntimeError(f"SOOP comment API HTTP {exc.code} for {title_no} page {page_number}") from exc
        except URLError as exc:
            raise RuntimeError(f"SOOP comment API connection failed for {title_no} page {page_number}") from exc

    def _should_scan_comment_support(
        self,
        raw_vod: Dict[str, Any],
        comment_checks: Dict[str, Any],
    ) -> bool:
        title_no = str(raw_vod["title_no"])
        counts = raw_vod.get("count", {})
        pure_views = safe_int(counts.get("vod_read_cnt"))
        comment_count = safe_int(counts.get("comment_cnt"))
        cached = comment_checks.get(title_no, {})

        if cached.get("supported"):
            return False
        if comment_count <= 0:
            return False
        if self.settings.streamer_tier == "partner":
            return False
        if self.settings.streamer_tier == "best" and pure_views > 1000:
            return False
        if cached.get("error"):
            return True
        if not cached.get("checked_at"):
            return True
        return safe_int(cached.get("comment_count")) != comment_count

    def _scan_comment_support(self, raw_vod: Dict[str, Any]) -> Dict[str, Any]:
        title_no = str(raw_vod["title_no"])
        comment_count = safe_int(raw_vod.get("count", {}).get("comment_cnt"))
        checked_at = datetime.now(timezone.utc).isoformat()
        scanned_pages = 0

        try:
            total_pages = max(1, math.ceil(comment_count / COMMENT_ROWS_PER_PAGE))
            for page_number in range(1, total_pages + 1):
                payload = self._fetch_comment_page(title_no, page_number)
                scanned_pages = page_number
                data = payload.get("data", {})
                payload_comment_count = safe_int(data.get("comment_cnt"))
                if payload_comment_count:
                    comment_count = payload_comment_count

                evidence = extract_support_evidence(payload)
                if evidence:
                    return {
                        "title_no": title_no,
                        "comment_count": comment_count,
                        "checked_at": checked_at,
                        "pages_scanned": scanned_pages,
                        **evidence,
                    }

                if not data.get("has_more"):
                    break

            return {
                "title_no": title_no,
                "supported": False,
                "comment_count": comment_count,
                "checked_at": checked_at,
                "pages_scanned": scanned_pages,
                "error": "",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "title_no": title_no,
                "supported": False,
                "comment_count": comment_count,
                "checked_at": checked_at,
                "pages_scanned": scanned_pages,
                "error": str(exc),
            }

    @staticmethod
    def _build_summary(vods: list[Dict[str, Any]]) -> Dict[str, Any]:
        summary = {
            "total": len(vods),
            "policy_day_delete": 0,
            "soon_after_policy": 0,
            "other_count": 0,
            "views_900_plus": 0,
            "views_1000_plus": 0,
            "later_delete": 0,
            "future_permanent": 0,
            "confirmed": 0,
            "auto_confirmed": 0,
            "api_auto_delete": 0,
            "needs_pre_policy_support": 0,
        }
        for vod in vods:
            if vod["future_permanent"]:
                summary["future_permanent"] += 1
            if vod["views_900_plus"]:
                summary["views_900_plus"] += 1
            if vod["views_1000_plus"]:
                summary["views_1000_plus"] += 1

            if vod["support_confirmed"]:
                summary["confirmed"] += 1
                summary["auto_confirmed"] += 1
            elif vod["delete_on_policy_day"]:
                summary["policy_day_delete"] += 1
            elif vod["urgency"] == "soon":
                summary["soon_after_policy"] += 1
            else:
                summary["other_count"] += 1
                if vod["urgency"] == "later":
                    summary["later_delete"] += 1

            if vod["api_auto_delete_flag"]:
                summary["api_auto_delete"] += 1
            if vod["needs_pre_policy_support"]:
                summary["needs_pre_policy_support"] += 1
        return summary


class RequestHandler(BaseHTTPRequestHandler):
    monitor: SoopReplayMonitor

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._send_json(self.monitor.snapshot())
            return

        if parsed.path == "/":
            self._serve_static_file(STATIC_DIR / "index.html")
            return

        if parsed.path.startswith("/static/"):
            relative = parsed.path.removeprefix("/static/")
            self._serve_static_file(STATIC_DIR / relative)
            return

        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/refresh":
            self.monitor.trigger_refresh()
            self._send_json({"ok": True})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return

    def _send_json(self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _serve_static_file(self, path: Path) -> None:
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if STATIC_DIR not in resolved.parents and resolved != STATIC_DIR / "index.html":
            self.send_error(HTTPStatus.FORBIDDEN)
            return

        content_type, _ = mimetypes.guess_type(resolved.name)
        if resolved.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif resolved.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif resolved.suffix == ".html":
            content_type = "text/html; charset=utf-8"

        content = resolved.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)


def build_settings() -> MonitorSettings:
    display_name = os.environ.get("SOOP_DISPLAY_NAME", DEFAULT_DISPLAY_NAME).strip() or DEFAULT_DISPLAY_NAME
    return MonitorSettings(
        streamer_id=os.environ.get("SOOP_STREAMER_ID", DEFAULT_STREAMER_ID).strip() or DEFAULT_STREAMER_ID,
        display_name=display_name,
        page_title=format_display_text(os.environ.get("SOOP_PAGE_TITLE"), display_name, DEFAULT_PAGE_TITLE),
        page_heading=format_display_text(os.environ.get("SOOP_PAGE_HEADING"), display_name, DEFAULT_PAGE_HEADING),
        streamer_tier=normalize_tier(os.environ.get("SOOP_STREAMER_TIER", DEFAULT_TIER)),
        policy_date=parse_policy_date(os.environ.get("SOOP_POLICY_DATE", DEFAULT_POLICY_DATE.isoformat())),
        poll_interval_seconds=max(30, int(os.environ.get("SOOP_POLL_INTERVAL_SECONDS", DEFAULT_POLL_INTERVAL_SECONDS))),
        host=os.environ.get("SOOP_HOST", "127.0.0.1"),
        port=int(os.environ.get("SOOP_PORT", "8000")),
    )


def main() -> None:
    settings = build_settings()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RequestHandler.monitor = SoopReplayMonitor(settings)
    RequestHandler.monitor.start()

    server = ThreadingHTTPServer((settings.host, settings.port), RequestHandler)
    try:
        print(
            f"Serving SOOP replay monitor for {settings.streamer_id} [{settings.display_name}] "
            f"({settings.streamer_tier}) on http://{settings.host}:{settings.port}"
        )
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        RequestHandler.monitor.stop()


if __name__ == "__main__":
    main()
