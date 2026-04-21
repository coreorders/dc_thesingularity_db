import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup


KST = timezone(timedelta(hours=9))
DATA_DIR = Path("data")
STATE_PATH = DATA_DIR / "state.json"
POSTS_PATH = DATA_DIR / "posts.json"
COMMENTS_PATH = DATA_DIR / "comments.json"


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def ensure_data_files() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_PATH.exists():
        write_json(
            STATE_PATH,
            {
                "last_success_at": None,
                "last_seen_post_id": 0,
                "last_run_at": None,
                "run_count": 0,
            },
        )
    if not POSTS_PATH.exists():
        write_json(POSTS_PATH, {})
    if not COMMENTS_PATH.exists():
        write_json(COMMENTS_PATH, {})


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, sort_keys=True)
    tmp.replace(path)


def normalize_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def to_int(value: Optional[str], default: int = 0) -> int:
    if value is None:
        return default
    m = re.search(r"-?\d+", value.replace(",", ""))
    if not m:
        return default
    try:
        return int(m.group(0))
    except ValueError:
        return default


def parse_dc_datetime(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    text = normalize_ws(raw)
    candidates = [
        "%Y.%m.%d %H:%M:%S",
        "%Y.%m.%d %H:%M",
        "%y.%m.%d %H:%M:%S",
        "%y.%m.%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y.%m.%d",
        "%y.%m.%d",
    ]
    for fmt in candidates:
        try:
            dt = datetime.strptime(text, fmt)
            if "%H" not in fmt:
                dt = dt.replace(hour=0, minute=0, second=0)
            return dt.replace(tzinfo=KST)
        except ValueError:
            continue
    return None


def dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    if not dt:
        return None
    return dt.astimezone(KST).isoformat()


def iso_to_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


@dataclass
class Config:
    gallery_id: str
    gallery_type: str
    max_pages: int
    always_refresh_pages: int
    comment_delay_minutes: int
    request_timeout_seconds: int
    user_agent: str

    @property
    def list_base_url(self) -> str:
        if self.gallery_type == "major":
            return "https://gall.dcinside.com/board/lists/"
        if self.gallery_type == "minor":
            return "https://gall.dcinside.com/mgallery/board/lists/"
        if self.gallery_type == "mini":
            return "https://gall.dcinside.com/mini/board/lists/"
        raise ValueError("DC_GALLERY_TYPE must be one of: major, minor, mini")

    @property
    def view_base_url(self) -> str:
        if self.gallery_type == "major":
            return "https://gall.dcinside.com/board/view/"
        if self.gallery_type == "minor":
            return "https://gall.dcinside.com/mgallery/board/view/"
        if self.gallery_type == "mini":
            return "https://gall.dcinside.com/mini/board/view/"
        raise ValueError("DC_GALLERY_TYPE must be one of: major, minor, mini")

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "User-Agent": self.user_agent,
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": self.list_base_url,
        }


def load_config() -> Config:
    return Config(
        gallery_id=os.environ.get("DC_GALLERY_ID", "").strip(),
        gallery_type=os.environ.get("DC_GALLERY_TYPE", "minor").strip().lower(),
        max_pages=max(1, int(os.environ.get("DC_MAX_LIST_PAGES", "60"))),
        always_refresh_pages=max(1, int(os.environ.get("DC_ALWAYS_REFRESH_PAGES", "3"))),
        comment_delay_minutes=max(1, int(os.environ.get("DC_COMMENT_DELAY_MINUTES", "20"))),
        request_timeout_seconds=max(5, int(os.environ.get("DC_TIMEOUT_SECONDS", "20"))),
        user_agent=os.environ.get(
            "DC_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ),
    )


def extract_post_id_from_href(href: str) -> Optional[int]:
    if not href:
        return None
    q = parse_qs(urlparse(href).query)
    no = q.get("no", [None])[0]
    return to_int(no, default=0) or None


def parse_list_posts(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    posts: List[Dict[str, Any]] = []

    rows = soup.select("tr.ub-content")
    if not rows:
        rows = soup.select("tbody > tr")

    for row in rows:
        num_cell = row.select_one("td.gall_num")
        raw_no = normalize_ws(num_cell.get_text(" ", strip=True) if num_cell else "")
        if not raw_no.isdigit():
            continue
        post_id = int(raw_no)

        link = row.select_one("a[href*='view']")
        href = link.get("href", "") if link else ""
        title = normalize_ws(link.get_text(" ", strip=True) if link else "")
        title = re.sub(r"\[\d+\]$", "", title).strip()
        if not title:
            title = normalize_ws(row.select_one("td.gall_tit").get_text(" ", strip=True)) if row.select_one("td.gall_tit") else ""

        writer_cell = row.select_one("td.gall_writer")
        writer_name = normalize_ws(writer_cell.get("data-nick", "") if writer_cell else "")
        writer_uid = normalize_ws(writer_cell.get("data-uid", "") if writer_cell else "")
        writer_ip = normalize_ws(writer_cell.get("data-ip", "") if writer_cell else "")
        writer_text = normalize_ws(writer_cell.get_text(" ", strip=True) if writer_cell else "")

        date_cell = row.select_one("td.gall_date")
        created_raw = ""
        if date_cell:
            created_raw = date_cell.get("title") or date_cell.get("data-time") or date_cell.get_text(" ", strip=True)

        view_cell = row.select_one("td.gall_count")
        reco_cell = row.select_one("td.gall_recommend")

        comment_count = 0
        cmt_el = row.select_one(".reply_num, .num, em.reply_num")
        if cmt_el:
            comment_count = to_int(cmt_el.get_text(" ", strip=True), default=0)

        posts.append(
            {
                "post_id": post_id,
                "post_url_hint": href,
                "title": title,
                "writer_name": writer_name,
                "writer_uid": writer_uid,
                "writer_ip": writer_ip,
                "writer_text": writer_text,
                "created_raw": normalize_ws(created_raw),
                "views": to_int(view_cell.get_text(" ", strip=True) if view_cell else "0"),
                "recommends": to_int(reco_cell.get_text(" ", strip=True) if reco_cell else "0"),
                "comment_count_in_list": comment_count,
            }
        )
    return posts


def parse_post_detail(html: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    title_el = (
        soup.select_one(".title_subject")
        or soup.select_one(".view_content_wrap h3")
        or soup.select_one("h3")
    )
    title = normalize_ws(title_el.get_text(" ", strip=True) if title_el else "")

    body_el = (
        soup.select_one(".write_div")
        or soup.select_one(".view_content_wrap .inner")
        or soup.select_one(".view_content")
    )
    body_text = ""
    if body_el:
        for bad in body_el.select("script, style, iframe, img, video, figure"):
            bad.decompose()
        body_text = body_el.get_text("\n", strip=True)
    body_text = normalize_ws(body_text.replace("\xa0", " "))

    writer_cell = soup.select_one(".gall_writer, .ub-writer, .nickname")
    writer_name = normalize_ws(writer_cell.get("data-nick", "") if writer_cell else "")
    writer_uid = normalize_ws(writer_cell.get("data-uid", "") if writer_cell else "")
    writer_ip = normalize_ws(writer_cell.get("data-ip", "") if writer_cell else "")
    writer_text = normalize_ws(writer_cell.get_text(" ", strip=True) if writer_cell else "")

    date_el = soup.select_one(".gall_date, .fr time, time")
    created_raw = ""
    if date_el:
        created_raw = date_el.get("title") or date_el.get("datetime") or date_el.get_text(" ", strip=True)
    created_raw = normalize_ws(created_raw)

    views_text = ""
    recom_text = ""
    for li in soup.select(".gallinfo li"):
        t = normalize_ws(li.get_text(" ", strip=True))
        if "조회" in t:
            views_text = t
        if "추천" in t:
            recom_text = t
    if not views_text:
        v_el = soup.select_one(".gall_count")
        views_text = v_el.get_text(" ", strip=True) if v_el else ""
    if not recom_text:
        r_el = soup.select_one(".gall_recommend")
        recom_text = r_el.get_text(" ", strip=True) if r_el else ""

    return {
        "title": title,
        "body_text": body_text,
        "writer_name": writer_name,
        "writer_uid": writer_uid,
        "writer_ip": writer_ip,
        "writer_text": writer_text,
        "created_raw": created_raw,
        "views": to_int(views_text, default=0),
        "recommends": to_int(recom_text, default=0),
    }


def parse_comments_from_detail(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    items: List[Dict[str, Any]] = []

    comment_nodes = soup.select("li.ub-content, .comment_wrap li, .cmt_list li")
    for idx, node in enumerate(comment_nodes, start=1):
        text_el = node.select_one(".usertxt, .comment_dccon, .comment")
        text = normalize_ws(text_el.get_text(" ", strip=True) if text_el else node.get_text(" ", strip=True))
        if not text:
            continue

        writer = node.select_one(".gall_writer, .nickname, .writer")
        writer_name = normalize_ws(writer.get("data-nick", "") if writer else "")
        writer_uid = normalize_ws(writer.get("data-uid", "") if writer else "")
        writer_ip = normalize_ws(writer.get("data-ip", "") if writer else "")
        writer_text = normalize_ws(writer.get_text(" ", strip=True) if writer else "")

        date_el = node.select_one(".date_time, .fr, time")
        created_raw = normalize_ws(
            (date_el.get("title") or date_el.get("datetime") or date_el.get_text(" ", strip=True)) if date_el else ""
        )

        raw_id = node.get("data-no") or node.get("data-c_no") or node.get("data-comment-no") or ""
        comment_id = to_int(raw_id, default=0)
        if comment_id == 0:
            comment_id = idx

        items.append(
            {
                "comment_id": comment_id,
                "text": text,
                "writer_name": writer_name,
                "writer_uid": writer_uid,
                "writer_ip": writer_ip,
                "writer_text": writer_text,
                "created_raw": created_raw,
                "created_at": dt_to_iso(parse_dc_datetime(created_raw)),
                "is_reply": "reply" in (node.get("class") or []),
            }
        )
    return items


def fetch(session: requests.Session, url: str, config: Config) -> str:
    r = session.get(url, headers=config.headers, timeout=config.request_timeout_seconds)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or "utf-8"
    return r.text


def list_url(config: Config, page: int) -> str:
    return f"{config.list_base_url}?id={config.gallery_id}&page={page}"


def view_url(config: Config, post_id: int) -> str:
    return f"{config.view_base_url}?id={config.gallery_id}&no={post_id}"


def pick_candidate_post_ids(
    session: requests.Session,
    config: Config,
    last_seen_post_id: int,
) -> Tuple[List[int], int]:
    candidate_ids: List[int] = []
    newest_seen = last_seen_post_id
    old_streak = 0

    for page in range(1, config.max_pages + 1):
        html = fetch(session, list_url(config, page), config)
        rows = parse_list_posts(html)
        if not rows:
            break

        page_ids = [r["post_id"] for r in rows if isinstance(r.get("post_id"), int)]
        if page_ids:
            newest_seen = max(newest_seen, max(page_ids))

        if page <= config.always_refresh_pages:
            candidate_ids.extend(page_ids)

        for pid in page_ids:
            if pid > last_seen_post_id:
                candidate_ids.append(pid)
                old_streak = 0
            else:
                old_streak += 1

        if page > config.always_refresh_pages and old_streak >= 40:
            break

    deduped = sorted(set(candidate_ids), reverse=True)
    return deduped, newest_seen


def upsert_post(
    posts_db: Dict[str, Any],
    post_id: int,
    detail: Dict[str, Any],
    now_iso: str,
    comment_delay_minutes: int,
) -> None:
    key = str(post_id)
    prev = posts_db.get(key, {})

    created_dt = parse_dc_datetime(detail.get("created_raw"))
    created_iso = dt_to_iso(created_dt)
    due_iso = dt_to_iso(created_dt + timedelta(minutes=comment_delay_minutes)) if created_dt else None

    merged = {
        **prev,
        **detail,
        "post_id": post_id,
        "post_url": detail.get("post_url"),
        "created_at": created_iso or prev.get("created_at"),
        "comment_due_at": due_iso or prev.get("comment_due_at"),
        "last_collected_at": now_iso,
        "first_seen_at": prev.get("first_seen_at") or now_iso,
    }
    posts_db[key] = merged


def collect_due_comment_post_ids(posts_db: Dict[str, Any], now: datetime) -> List[int]:
    due: List[int] = []
    for key, post in posts_db.items():
        if post.get("comments_fetched_at"):
            continue

        created_at = iso_to_dt(post.get("created_at"))
        due_at = iso_to_dt(post.get("comment_due_at"))
        first_seen_at = iso_to_dt(post.get("first_seen_at"))

        ready = False
        if due_at and due_at <= now:
            ready = True
        elif created_at and created_at + timedelta(minutes=20) <= now:
            ready = True
        elif first_seen_at and first_seen_at + timedelta(minutes=20) <= now:
            ready = True

        if ready:
            try:
                due.append(int(key))
            except ValueError:
                continue

    due.sort(reverse=True)
    return due


def main() -> int:
    ensure_data_files()
    config = load_config()
    if not config.gallery_id:
        raise RuntimeError("DC_GALLERY_ID is required")

    state = read_json(STATE_PATH, {})
    posts_db: Dict[str, Any] = read_json(POSTS_PATH, {})
    comments_db: Dict[str, Any] = read_json(COMMENTS_PATH, {})

    last_seen_post_id = int(state.get("last_seen_post_id") or 0)
    now = now_kst()
    now_iso = dt_to_iso(now)

    session = requests.Session()
    session.headers.update(config.headers)

    candidates, newest_seen = pick_candidate_post_ids(session, config, last_seen_post_id)

    post_fetch_ok = 0
    post_fetch_fail = 0
    for pid in candidates:
        try:
            url = view_url(config, pid)
            html = fetch(session, url, config)
            detail = parse_post_detail(html)
            detail["post_url"] = url
            upsert_post(posts_db, pid, detail, now_iso, config.comment_delay_minutes)
            post_fetch_ok += 1
        except Exception:
            post_fetch_fail += 1
            continue

    due_comment_ids = collect_due_comment_post_ids(posts_db, now)
    comment_fetch_ok = 0
    comment_fetch_fail = 0
    for pid in due_comment_ids:
        try:
            url = view_url(config, pid)
            html = fetch(session, url, config)
            comments = parse_comments_from_detail(html)
            comments_db[str(pid)] = {
                "post_id": pid,
                "post_url": url,
                "fetched_at": now_iso,
                "comments": comments,
                "comment_count": len(comments),
            }
            post = posts_db.get(str(pid), {})
            post["comments_fetched_at"] = now_iso
            post["comments_count_final"] = len(comments)
            posts_db[str(pid)] = post
            comment_fetch_ok += 1
        except Exception:
            comment_fetch_fail += 1
            continue

    next_state = {
        **state,
        "last_run_at": now_iso,
        "run_count": int(state.get("run_count") or 0) + 1,
        "last_seen_post_id": max(last_seen_post_id, newest_seen),
        "last_success_at": now_iso,
        "last_stats": {
            "post_candidates": len(candidates),
            "post_fetch_ok": post_fetch_ok,
            "post_fetch_fail": post_fetch_fail,
            "comment_targets": len(due_comment_ids),
            "comment_fetch_ok": comment_fetch_ok,
            "comment_fetch_fail": comment_fetch_fail,
        },
    }

    write_json(POSTS_PATH, posts_db)
    write_json(COMMENTS_PATH, comments_db)
    write_json(STATE_PATH, next_state)

    print(
        json.dumps(
            {
                "ok": True,
                "now": now_iso,
                "gallery_id": config.gallery_id,
                "gallery_type": config.gallery_type,
                "post_candidates": len(candidates),
                "post_fetch_ok": post_fetch_ok,
                "post_fetch_fail": post_fetch_fail,
                "comment_targets": len(due_comment_ids),
                "comment_fetch_ok": comment_fetch_ok,
                "comment_fetch_fail": comment_fetch_fail,
                "last_seen_post_id": next_state["last_seen_post_id"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
