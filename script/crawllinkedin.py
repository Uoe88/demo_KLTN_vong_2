"""
LinkedIn Job Crawler - Phase 1: Raw Data Collection

--------------------------------------------------------------------
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://www.linkedin.com"
SOURCE_ID = "LNK"


SEARCH_API = f"{BASE_URL}/jobs-guest/jobs/api/seeMoreJobPostings/search"


DETAIL_API = f"{BASE_URL}/jobs-guest/jobs/api/jobPosting"


DEFAULT_LOCATION = "Vietnam"
DEFAULT_GEO_ID = "104195383"

JOB_ID_RE = re.compile(r"(\d{8,})")

EXPERTISE_QUERIES: dict[str, list[str]] = {
    "Architect": [
        "Software Architect",
        "Solution Architect",
        "Technical Architect",
    ],
    "Security": [
        "Security Engineer",
        "Information Security",
        "Cyber Security Engineer",
    ],
    "Fullstack": [
        "Fullstack Developer",
        "Full Stack Engineer",
    ],
    "Frontend": [
        "Frontend Developer",
        "Front End Engineer",
    ],
    "DevOps/SRE": [
        "DevOps Engineer",
        "Site Reliability Engineer",
        "Cloud Engineer",
    ],
    "Mobile": [
        "Mobile Developer",
        "Android Developer",
        "iOS Developer",
        "Flutter Developer",
    ],
}

# Alias nhận diện domain linh hoạt (không phân biệt hoa-thường).
DOMAIN_ALIASES: dict[str, str] = {
    "architect": "Architect",
    "software-architect": "Architect",
    "solution-architect": "Architect",
    "technical-architect": "Architect",
    "security": "Security",
    "security-engineer": "Security",
    "sec": "Security",
    "cyber": "Security",
    "cyber-security": "Security",
    "fullstack": "Fullstack",
    "full-stack": "Fullstack",
    "fullstack-developer": "Fullstack",
    "fs": "Fullstack",
    "frontend": "Frontend",
    "front-end": "Frontend",
    "frontend-developer": "Frontend",
    "fe": "Frontend",
    "devops": "DevOps/SRE",
    "devops/sre": "DevOps/SRE",
    "devops-engineer": "DevOps/SRE",
    "sre": "DevOps/SRE",
    "site-reliability-engineer": "DevOps/SRE",
    "cloud": "DevOps/SRE",
    "cloud-engineer": "DevOps/SRE",
    "mobile": "Mobile",
    "mobile-developer": "Mobile",
    "android": "Mobile",
    "ios": "Mobile",
    "flutter": "Mobile",
}


def resolve_domain_key(token: str) -> str | None:
    """Trả về đúng key trong EXPERTISE_QUERIES ứng với `token` người
    dùng gõ: khớp chính xác, khác hoa-thường, hoặc qua alias. Trả None
    nếu không nhận diện được."""
    t = token.strip()
    if not t:
        return None

    for key in EXPERTISE_QUERIES:
        if key.casefold() == t.casefold():
            return key

    return DOMAIN_ALIASES.get(t.casefold())


# ============================================================
# SESSION
# ============================================================

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]


class RateLimited(Exception):
    """Bị LinkedIn trả 429 (hoặc 999) — cần dừng/nghỉ, không retry vô hạn."""


def make_session() -> requests.Session:
    session = requests.Session()
    # KHÔNG đưa 429 vào status_forcelist: LinkedIn trả 429 khi đã nghi
    # ngờ bot, retry dồn dập chỉ làm bị chặn nhanh hơn. 429 được xử lý
    # riêng ở fetch_html() bằng backoff dài.
    retries = Retry(
        total=3,
        backoff_factor=1.5,
        status_forcelist=(500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update(
        {
            "User-Agent": random.choice(USER_AGENTS),
            "Accept-Language": "en-US,en;q=0.9,vi;q=0.8",
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "X-Requested-With": "XMLHttpRequest",
        }
    )
    return session


def fetch_html(
    url: str,
    session: requests.Session,
    *,
    max_429: int = 3,
) -> str:
    """GET một URL, xử lý riêng 429/999 bằng backoff dài. Ném
    RateLimited nếu vẫn bị chặn sau `max_429` lần chờ."""
    wait = 30.0
    for attempt in range(max_429 + 1):
        response = session.get(url, timeout=30)

        # 999 là mã "Request denied" riêng của LinkedIn cho bot.
        if response.status_code in (429, 999):
            if attempt == max_429:
                raise RateLimited(
                    f"HTTP {response.status_code} sau {max_429} lần chờ: {url}"
                )
            print(
                f"  [RATE LIMIT] HTTP {response.status_code}, nghỉ {wait:.0f}s "
                f"rồi thử lại ({attempt + 1}/{max_429})...",
                file=sys.stderr,
            )
            time.sleep(wait)
            wait *= 2
            # Đổi User-Agent cho lần thử sau
            session.headers["User-Agent"] = random.choice(USER_AGENTS)
            continue

        response.raise_for_status()
        return response.text

    raise RateLimited(f"Không lấy được: {url}")


# ============================================================
# TEXT CLEANING & EXCEL ESCAPING
# ============================================================

def clean_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    # Loại bỏ dấu '=' ở đầu để tránh lỗi #NAME? khi mở CSV trong Excel
    if cleaned.startswith("="):
        cleaned = "'" + cleaned
    return cleaned


def clean_block(value: str) -> str:
    """Giữ ngắt dòng (JD nhiều gạch đầu dòng) nhưng bỏ dòng trống thừa."""
    if not value:
        return ""
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in value.splitlines()]
    lines = [ln for ln in lines if ln]
    text = "\n".join(lines)
    if text.startswith("="):
        text = "'" + text
    return text


# ============================================================
# URL / ID HELPERS
# ============================================================

def extract_job_id(value: str) -> str:
    """Lấy job id dạng số từ URL hoặc từ data-entity-urn."""
    if not value:
        return ""
    # urn:li:jobPosting:4012345678
    if "jobPosting:" in value:
        tail = value.rsplit("jobPosting:", 1)[1]
        match = JOB_ID_RE.search(tail)
        if match:
            return match.group(1)
    # .../jobs/view/frontend-developer-at-abc-4012345678?...
    path = urlparse(value).path
    matches = JOB_ID_RE.findall(path)
    if matches:
        return matches[-1]
    return ""


def canonical_job_url(job_id: str) -> str:
    return f"{BASE_URL}/jobs/view/{job_id}" if job_id else ""


def build_search_url(
    keyword: str,
    *,
    location: str,
    geo_id: str,
    start: int,
    f_tpr: str | None,
) -> str:
    params: dict[str, Any] = {
        "keywords": keyword,
        "location": location,
        "start": start,
        # DD = sắp xếp theo ngày đăng giảm dần -> job mới nhất trước,
        # giúp dừng sớm khi đã vượt quá --from-date.
        "sortBy": "DD",
    }
    if geo_id:
        params["geoId"] = geo_id
    if f_tpr:
        params["f_TPR"] = f_tpr
    return f"{SEARCH_API}?{urlencode(params)}"


def build_detail_url(job_id: str) -> str:
    return f"{DETAIL_API}/{job_id}"


# ============================================================
# DATE HANDLING
# ============================================================

_RELATIVE_TIME_RE = re.compile(
    r"(\d+)\s*(hours?|giờ|days?|ngày|weeks?|tuần|months?|tháng)"
)


def parse_posted_date(raw: str, reference: datetime | None = None) -> date | None:
    """Chuyển updated_at thành date để so sánh. Trả None nếu không
    parse được — job đó được GIỮ LẠI mặc định (an toàn hơn loại nhầm)."""
    if not raw:
        return None

    text = raw.strip()

    # 1. ISO 8601 / YYYY-MM-DD — nguồn chính (<time datetime="...">)
    try:
        iso_text = text[:-1] + "+00:00" if text.endswith("Z") else text
        return datetime.fromisoformat(iso_text).date()
    except ValueError:
        pass

    # 2. Dự phòng: cụm tương đối "X ngày/giờ/tuần/tháng trước|ago"
    reference = reference or datetime.now()
    match = _RELATIVE_TIME_RE.search(text.casefold())
    if match:
        amount = int(match.group(1))
        unit = match.group(2)

        if unit.startswith(("hour", "giờ")):
            delta = timedelta(hours=amount)
        elif unit.startswith(("day", "ngày")):
            delta = timedelta(days=amount)
        elif unit.startswith(("week", "tuần")):
            delta = timedelta(weeks=amount)
        else:  # month / tháng — ước lượng 30 ngày
            delta = timedelta(days=amount * 30)

        return (reference - delta).date()

    lowered = text.casefold()
    if "today" in lowered or "hôm nay" in lowered:
        return reference.date()
    if "yesterday" in lowered or "hôm qua" in lowered:
        return (reference - timedelta(days=1)).date()

    return None


def is_within_date_range(
    updated_at: str,
    date_from: date | None,
    date_to: date | None,
) -> bool:
    """True nếu KHÔNG có bộ lọc, hoặc job nằm trong khoảng, hoặc không
    parse được ngày (giữ lại mặc định thay vì loại nhầm do lỗi parse)."""
    if date_from is None and date_to is None:
        return True

    posted = parse_posted_date(updated_at)
    if posted is None:
        return True
    if date_from is not None and posted < date_from:
        return False
    if date_to is not None and posted > date_to:
        return False
    return True


# LinkedIn KHÔNG chấp nhận f_TPR tùy ý một cách đáng tin cậy — trên UI
# chỉ có đúng 3 mốc lọc "Date posted": Past 24 hours / Past week / Past
# month. Giá trị f_TPR khác 3 mốc này VẪN có thể được server chấp nhận
# nhưng không đảm bảo hoạt động đúng, và quá 30 ngày thì job coi như
# không còn được index để trả về nữa. Vì vậy thay vì tính số giây tùy
# ý theo --from-date, ta CHỐT vào mốc chính thức gần nhất, đủ rộng để
# phủ --from-date.
TPR_PRESETS: list[tuple[int, str]] = [
    (1, "r86400"),      # Past 24 hours
    (7, "r604800"),     # Past week
    (30, "r2592000"),   # Past month — mốc XA NHẤT còn tin cậy
]


def derive_f_tpr(date_from: date | None) -> str | None:
    """Chốt f_TPR vào 1 trong 3 mốc CHÍNH THỨC của LinkedIn (24h / 1
    tuần / 1 tháng), chọn mốc NHỎ NHẤT vẫn đủ phủ --from-date. Nếu
    --from-date xa hơn 30 ngày, vẫn dùng mốc 1 tháng (xa nhất có thể)
    — phần vượt quá sẽ được cảnh báo riêng ở warn_date_range_reachable.
    Trả None nếu không có --from-date (không lọc phía server, chỉ dựa
    vào sortBy=DD + lọc phía client)."""
    if date_from is None:
        return None
    days_needed = (date.today() - date_from).days + 1
    for max_days, f_tpr in TPR_PRESETS:
        if days_needed <= max_days:
            return f_tpr
    return TPR_PRESETS[-1][1]  # xa hơn 30 ngày -> vẫn chốt ở mốc 1 tháng


def warn_date_range_reachable(date_from: date | None, date_to: date | None) -> None:
    """LinkedIn chỉ lọc theo 3 mốc CHÍNH THỨC (24h/tuần/tháng) và thực
    tế không còn index job cũ hơn ~30 ngày. Cảnh báo rõ để không hiểu
    nhầm 'crawl xong mà thiếu job'."""
    today = date.today()

    if date_to and date_to > today:
        print(
            f"[CẢNH BÁO] --to-date ({date_to:%d/%m/%Y}) nằm ở TƯƠNG LAI. "
            f"Hôm nay là {today:%d/%m/%Y}; job chưa đăng thì chưa thể crawl. "
            "Hãy chạy lại script MỖI NGÀY (kèm --append) cho tới ngày đó — "
            "xem phần 'cách chạy hằng ngày' trong hướng dẫn.",
            file=sys.stderr,
        )

    if date_from:
        age = (today - date_from).days
        if age > 30:
            unreachable_to = today - timedelta(days=30)
            print(
                f"[CẢNH BÁO] --from-date ({date_from:%d/%m/%Y}) cách đây {age} ngày. "
                "LinkedIn chỉ hỗ trợ lọc chính thức theo 24 giờ / 1 tuần / 1 tháng "
                "gần nhất, và cũng không còn index job cũ hơn ~30 ngày "
                f"(tức trước ~{unreachable_to:%d/%m/%Y}). Script sẽ dùng mốc "
                "'Past month' (f_TPR=r2592000, xa nhất còn tin cậy), nhưng phần "
                f"{date_from:%d/%m/%Y}–{unreachable_to:%d/%m/%Y} GẦN NHƯ CHẮC CHẮN "
                "không lấy lại được bằng crawl trực tiếp — cần nguồn dữ liệu lưu "
                "trữ (Coresignal, Bright Data...) hoặc file CSV đã crawl trước đó.",
                file=sys.stderr,
            )
        elif age > 7:
            print(
                f"[INFO] --from-date ({date_from:%d/%m/%Y}) cách đây {age} ngày -> "
                "dùng mốc lọc 'Past month' (f_TPR=r2592000).",
                file=sys.stderr,
            )
        elif age > 1:
            print(
                f"[INFO] --from-date ({date_from:%d/%m/%Y}) cách đây {age} ngày -> "
                "dùng mốc lọc 'Past week' (f_TPR=r604800).",
                file=sys.stderr,
            )
        else:
            print(
                "[INFO] --from-date trong vòng 24h -> dùng mốc lọc "
                "'Past 24 hours' (f_TPR=r86400) — độ tin cậy cao nhất.",
                file=sys.stderr,
            )


# ============================================================
# JSON-LD EXTRACTION
# ============================================================

def find_job_posting_json(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(script.get_text(strip=True))
        except (json.JSONDecodeError, TypeError):
            continue

        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                return candidate
    return {}


# ============================================================
# DOM HELPER — JOB CRITERIA CỦA LINKEDIN
# ============================================================

def job_criteria(soup: BeautifulSoup) -> dict[str, str]:
    """Đọc khối 'Seniority level / Employment type / Job function /
    Industries' ở trang chi tiết. Trả dict key viết thường."""
    result: dict[str, str] = {}
    for item in soup.select("li.description__job-criteria-item, li.job-criteria__item"):
        header = item.select_one(
            ".description__job-criteria-subheader, .job-criteria__subheader"
        )
        value = item.select_one(
            ".description__job-criteria-text, .job-criteria__text"
        )
        if header and value:
            key = clean_text(header.get_text(" ", strip=True)).casefold().rstrip(":")
            result[key] = clean_text(value.get_text(" ", strip=True))
    return result


def extract_full_description(soup: BeautifulSoup) -> str:
    """Lấy toàn bộ JD dưới dạng text, giữ ngắt dòng theo <br>/<li>/<p>."""
    container = soup.select_one(
        ".show-more-less-html__markup, .description__text, "
        ".jobs-description__content, .jobs-box__html-content"
    )
    if container is None:
        container = soup.select_one("section.description") or soup

    for tag in container.select("script, style, button"):
        tag.decompose()

    # Chèn ngắt dòng trước khi lấy text để không dính liền các bullet
    for br in container.find_all("br"):
        br.replace_with("\n")
    for tag in container.find_all(["li", "p", "div", "h1", "h2", "h3", "h4"]):
        tag.append("\n")

    return clean_block(container.get_text(" ", strip=False))


# --- Cắt JD thành Mô tả / Yêu cầu ---------------------------------

REQUIREMENT_HEADINGS = (
    "requirements",
    "requirement",
    "job requirements",
    "qualifications",
    "your skills and experience",
    "skills and experience",
    "skills & experience",
    "required skills",
    "must have",
    "must-have",
    "must haves",
    "what you need",
    "what you'll need",
    "what we are looking for",
    "what we're looking for",
    "who you are",
    "your profile",
    "candidate profile",
    "yêu cầu công việc",
    "yêu cầu ứng viên",
    "yêu cầu",
    "kỹ năng yêu cầu",
    "kinh nghiệm yêu cầu",
)

# Heading báo hiệu ĐÃ HẾT phần yêu cầu (phúc lợi, quy trình...)
END_HEADINGS = (
    "benefits",
    "what we offer",
    "why join us",
    "perks",
    "compensation",
    "our offer",
    "quyền lợi",
    "phúc lợi",
    "chế độ đãi ngộ",
)


def _matches_heading(line: str, headings: tuple[str, ...]) -> bool:
    text = line.strip().strip(":*-–—•# ").casefold()
    if not text or len(text) > 80:
        return False
    return any(text == h or text.startswith((f"{h}:", f"{h} ")) for h in headings)


def split_description_requirements(full_text: str) -> tuple[str, str]:
    """Cắt JD một khối của LinkedIn thành (description_raw,
    requirements_raw) dựa trên dòng heading. Không tìm thấy mốc cắt ->
    trả (toàn bộ JD, "")."""
    if not full_text:
        return "", ""

    lines = full_text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if _matches_heading(line, REQUIREMENT_HEADINGS):
            start = index
            break

    if start is None:
        return full_text, ""

    end = len(lines)
    for index in range(start + 1, len(lines)):
        if _matches_heading(lines[index], END_HEADINGS):
            end = index
            break

    description = "\n".join(lines[:start]).strip()
    requirements = "\n".join(lines[start:end]).strip()

    # JD bắt đầu ngay bằng "Requirements" -> đừng để description rỗng
    if not description:
        description = full_text

    return description, requirements


# ============================================================
# FIELD EXTRACTORS
# ============================================================

_SALARY_RE = re.compile(
    r"(?:"
    r"(?:USD|VND|\$|₫)\s?[\d.,]+\s*(?:[-–—to]+\s*(?:USD|VND|\$|₫)?\s?[\d.,]+)?"
    r"(?:\s*(?:k|K|tr|triệu|million|/\s*(?:month|year|tháng|năm)))?"
    r"|[\d.,]+\s*(?:[-–—]\s*[\d.,]+\s*)?(?:triệu|tr)\b"
    r"|up to\s+(?:USD|VND|\$)?\s?[\d.,]+"
    r")",
    re.IGNORECASE,
)


def extract_salary(soup: BeautifulSoup, schema: dict[str, Any], full_text: str) -> str:
    """LinkedIn hiếm khi công bố lương. Thứ tự: khối salary trên UI ->
    baseSalary trong JSON-LD -> regex quét trong JD."""
    node = soup.select_one(
        ".compensation__salary, .salary, .job-details-jobs-unified-top-card__job-insight"
    )
    if node:
        text = clean_text(node.get_text(" ", strip=True))
        if text and _SALARY_RE.search(text):
            return text

    base = schema.get("baseSalary")
    if isinstance(base, dict):
        value = base.get("value")
        currency = clean_text(str(base.get("currency", "")))
        if isinstance(value, dict):
            low = value.get("minValue")
            high = value.get("maxValue")
            single = value.get("value")
            unit = clean_text(str(value.get("unitText", "")))
            if low or high:
                parts = [str(p) for p in (low, high) if p]
                return clean_text(f"{currency} {' - '.join(parts)} {unit}")
            if single:
                return clean_text(f"{currency} {single} {unit}")

    if full_text:
        match = _SALARY_RE.search(full_text)
        if match:
            return clean_text(match.group(0))

    return ""


def extract_experience(full_text: str, schema: dict[str, Any]) -> str:
    """Số năm kinh nghiệm: ưu tiên experienceRequirements trong JSON-LD,
    sau đó regex quét JD (giống bản ITviec, có cả mẫu tiếng Việt)."""
    req = schema.get("experienceRequirements")
    if isinstance(req, dict):
        months = req.get("monthsOfExperience")
        if months:
            try:
                years = int(months) / 12
                return f"{years:.0f} years" if years >= 1 else f"{months} months"
            except (TypeError, ValueError):
                pass
    elif isinstance(req, str) and req.strip():
        return clean_text(req)

    if not full_text:
        return ""

    patterns = [
        r"\b\d+\s*(?:[-–—]\s*\d+\s*)?\+?\s*(?:years?|yrs?)(?:\s+of)?\s+experience\b",
        r"\b\d+\s*(?:[-–—]\s*\d+\s*)?năm\s+kinh\s*nghiệm\b",
        r"\b\d+\s*(?:[-–—]\s*\d+\s*)?\+?\s*(?:years?|yrs?)\b",
        r"\b\d+\s*(?:[-–—]\s*\d+\s*)?năm\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            result = clean_text(match.group(0))
            if result:
                return result
    return ""


def extract_level(criteria: dict[str, str], title: str) -> str:
    """Seniority level của LinkedIn, dự phòng bằng từ khóa trong tiêu đề."""
    seniority = criteria.get("seniority level", "")
    if seniority and seniority.casefold() not in ("not applicable", "n/a"):
        return seniority

    title_lower = title.lower()
    level_mapping = {
        "intern": "Intern",
        "fresher": "Fresher",
        "junior": "Junior",
        "middle": "Middle",
        "mid-level": "Middle",
        "senior": "Senior",
        "lead": "Lead",
        "principal": "Principal",
        "staff": "Staff",
        "manager": "Manager",
        "head of": "Head",
        "director": "Director",
    }
    found = [lvl for kw, lvl in level_mapping.items() if kw in title_lower]
    return " | ".join(dict.fromkeys(found)) if found else ""


def extract_location(soup: BeautifulSoup, schema: dict[str, Any], fallback: str) -> str:
    node = soup.select_one(
        ".topcard__flavor--bullet, .job-details-jobs-unified-top-card__bullet"
    )
    if node:
        text = clean_text(node.get_text(" ", strip=True))
        if text:
            return text

    job_location = schema.get("jobLocation")
    candidates = job_location if isinstance(job_location, list) else [job_location]
    parts: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        address = candidate.get("address")
        if isinstance(address, dict):
            chunk = [
                clean_text(str(address.get(key, "")))
                for key in ("addressLocality", "addressRegion", "addressCountry")
            ]
            joined = ", ".join(p for p in chunk if p)
            if joined:
                parts.append(joined)
    if parts:
        return " | ".join(dict.fromkeys(parts))

    return clean_text(fallback)


# ============================================================
# RAW SKILL EXTRACTION (PHASE 1 - KHÔNG CHUẨN HÓA)
# ============================================================

# Giữ nguyên bộ pattern của bản ITviec để hai nguồn dữ liệu so sánh
# được với nhau ở Phase 2, bổ sung thêm một số công cụ thiên về
# Security / DevOps / Mobile vốn xuất hiện nhiều trên LinkedIn.
RAW_SKILL_PATTERNS = [
    # --- Ngôn ngữ lập trình ---
    r"\bTypeScript\b", r"\bJavaScript\b", r"\bNode\.js\b",
    r"\bObjective-C\b", r"\bC\+\+\b", r"\bC#\b", r"\b\.NET\b",
    r"\bJava\b", r"\bKotlin\b", r"\bScala\b", r"\bGroovy\b",
    r"\bPython\b", r"\bGo(lang)?\b", r"\bRust\b", r"\bRuby\b", r"\bPHP\b",
    r"\bSwift\b", r"\bDart\b", r"\bBash\b", r"\bPowerShell\b",

    # --- Backend framework ---
    r"\bSpring Cloud\b", r"\bSpring Boot\b", r"\bSpring\b",
    r"\bRuby on Rails\b", r"\bASP\.NET( Core)?\b",
    r"\bDjango\b", r"\bFlask\b", r"\bFastAPI\b",
    r"\bExpress(\.js)?\b", r"\bNestJS\b",
    r"\bLaravel\b", r"\bSymfony\b", r"\bGin\b", r"\bEcho\b",

    # --- Frontend framework ---
    r"\bReact Native\b", r"\bNext\.js\b", r"\bNuxt(\.js)?\b", r"\bReact\b",
    r"\bVue\b", r"\bAngular\b", r"\bSvelte\b", r"\bRemix\b",
    r"\bTailwind( ?CSS)?\b", r"\bBootstrap\b", r"\bRedux\b",
    r"\bHTML5?\b", r"\bCSS3?\b", r"\bSASS\b", r"\bSCSS\b", r"\bWebpack\b",

    # --- Mobile ---
    r"\bJetpack Compose\b", r"\bSwiftUI\b", r"\bFlutter\b",
    r"\bAndroid\b", r"\biOS\b", r"\bXcode\b", r"\bKMM\b", r"\bXamarin\b",

    # --- CSDL / Database ---
    r"\bPostgre\s*SQL\b", r"\bPostgres\b",
    r"\bSQL\s*Server\b", r"\bMariaDB\b",
    r"\bMySQL\b", r"\bOracle\b", r"\bSQLite\b", r"\bMS\s*SQL\b", r"\bT-SQL\b", r"\bSQL\b",
    r"\bMongoDB\b", r"\bCassandra\b", r"\bDynamoDB\b", r"\bNoSQL\b",
    r"\bElasticsearch\b", r"\bOpenSearch\b", r"\bRedis\b",
    r"\bClickHouse\b", r"\bSnowflake\b", r"\bBigQuery\b", r"\bNeo4j\b",

    # --- ORM / Data access ---
    r"\bSQLAlchemy\b", r"\bHibernate\b", r"\bMyBatis\b",
    r"\bjOOQ\b", r"\bJPA\b", r"\bPrisma\b", r"\bTypeORM\b", r"\bSequelize\b",

    # --- Kiến trúc / Architecture ---
    r"\bEvent[- ]?Driven\b", r"\bSaga Pattern\b", r"\bMessage Queue\b",
    r"\bMicroservices\b", r"\bMonolith\b", r"\bServerless\b",
    r"\bDDD\b", r"\bCQRS\b", r"\bREST API\b", r"\bGraphQL\b",
    r"\bgRPC\b", r"\bWebSocket\b", r"\bSOAP\b",
    r"\bRabbitMQ\b", r"\bActiveMQ\b", r"\bKafka\b", r"\bPub/?Sub\b",
    r"\bTOGAF\b", r"\bC4 Model\b", r"\bUML\b",

    # --- Cloud / Hạ tầng ---
    r"\bGoogle Cloud\b", r"\bAlibaba Cloud\b", r"\bAWS\b", r"\bAzure\b", r"\bGCP\b",
    r"\bFirebase\b", r"\bSupabase\b", r"\bHeroku\b", r"\bVercel\b", r"\bNetlify\b",
    r"\bLambda\b", r"\bEC2\b", r"\bS3\b", r"\bECS\b", r"\bEKS\b",

    # --- DevOps / CI-CD ---
    r"\bGitHub Actions\b", r"\bGitLab CI\b", r"\bCircleCI\b",
    r"\bCI/CD\b", r"\bKubernetes\b", r"\bK8s\b", r"\bDocker\b",
    r"\bTerraform\b", r"\bAnsible\b", r"\bJenkins\b", r"\bPulumi\b",
    r"\bArgoCD\b", r"\bHelm\b", r"\bNginx\b", r"\bApache\b",
    r"\bPrometheus\b", r"\bGrafana\b", r"\bDatadog\b", r"\bELK\b",
    r"\bOpenTelemetry\b", r"\bIstio\b", r"\bVault\b",

    # --- Version control / Công cụ ---
    r"\bGitHub\b", r"\bGitLab\b", r"\bBitbucket\b", r"\bGit\b",
    r"\bJira\b", r"\bConfluence\b", r"\bPostman\b", r"\bSwagger\b", r"\bOpenAPI\b",

    # --- Testing ---
    r"\bPlaywright\b", r"\bSelenium\b", r"\bCypress\b",
    r"\bJUnit\b", r"\bMockito\b", r"\bPytest\b", r"\bJest\b", r"\bTestNG\b", r"\bK6\b",

    # --- Build tool ---
    r"\bGradle\b", r"\bMaven\b", r"\bVite\b",
    r"\bnpm\b", r"\byarn\b", r"\bpnpm\b",

    # --- AI / Dev tools ---
    r"\bClaude Code\b", r"\bClaude\b", r"\bCursor\b", r"\bCopilot\b",
    r"\bChatGPT\b", r"\bGemini\b", r"\bWindsurf\b",

    # --- Bảo mật / Security ---
    r"\bOAuth2?\b", r"\bKeycloak\b", r"\bJWT\b", r"\bSSO\b", r"\bSAML\b", r"\bOKTA\b",
    r"\bSIEM\b", r"\bSOC\b", r"\bIDS\b", r"\bIPS\b", r"\bWAF\b", r"\bDLP\b",
    r"\bSplunk\b", r"\bQRadar\b", r"\bWazuh\b", r"\bSnort\b",
    r"\bBurp Suite\b", r"\bMetasploit\b", r"\bNmap\b", r"\bWireshark\b",
    r"\bOWASP\b", r"\bPenetration Testing\b", r"\bPentest\b",
    r"\bISO\s?27001\b", r"\bPCI[- ]?DSS\b", r"\bNIST\b", r"\bSOC\s?2\b",
    r"\bCISSP\b", r"\bCEH\b", r"\bOSCP\b", r"\bCISM\b", r"\bCompTIA\b",
    r"\bSAST\b", r"\bDAST\b", r"\bDevSecOps\b", r"\bZero Trust\b",

    # --- Big Data / Data Engineering ---
    r"\bAirflow\b", r"\bHadoop\b", r"\bSpark\b", r"\bFlink\b", r"\bDbt\b", r"\bETL\b",

    # --- Design / Product / Collaboration tools ---
    r"\bFigma\b", r"\bSketch\b", r"\bAdobe XD\b", r"\bZeplin\b",
    r"\bInVision\b", r"\bMiro\b", r"\bCanva\b",
    r"\bNotion\b", r"\bTrello\b", r"\bAsana\b", r"\bClickUp\b",
    r"\bSlack\b", r"\bMicrosoft Teams\b",

    # --- Soft skill / Chứng chỉ ngôn ngữ ---
    r"\bTOEIC\b", r"\bIELTS\b", r"\bTOEFL\b",
    r"\bproblem[- ]solving\b", r"\bteamwork\b",
    r"\bcommunication skills?\b", r"\b(giao tiếp|tư duy phản biện)\b",
    r"\btime management\b", r"\bcritical thinking\b",
    r"\bleadership\b", r"\badaptability\b",
]


def _scan_skills_in_text(text: str) -> list[str]:
    """Quét text bằng RAW_SKILL_PATTERNS, giữ NGUYÊN VĂN cách viết
    hoa/thường xuất hiện trong JD (không chuẩn hóa)."""
    found: list[str] = []
    if not text:
        return found
    for pattern in RAW_SKILL_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            exact_word = match.group(0)
            if exact_word.casefold() not in {f.casefold() for f in found}:
                found.append(exact_word)
    return found


def extract_skills_raw(
    schema: dict[str, Any],
    criteria: dict[str, str],
    requirements_raw: str,
    description_raw: str,
) -> list[str]:
    """Trả về HỢP (union) của: skills trong JSON-LD + 'Job function' của
    LinkedIn + skill quét được trong phần Yêu cầu và Mô tả. KHÔNG dừng
    ở nguồn đầu tiên tìm thấy — mỗi nguồn bắt được một nhóm khác nhau."""
    combined: list[str] = []

    def _add_all(items):
        seen_lower = {s.casefold() for s in combined}
        for item in items:
            item = clean_text(str(item))
            if item and item.casefold() not in seen_lower:
                combined.append(item)
                seen_lower.add(item.casefold())

    # 1. JSON-LD Schema
    schema_skills = schema.get("skills")
    if schema_skills:
        if isinstance(schema_skills, list):
            _add_all(str(s) for s in schema_skills if str(s).strip())
        elif isinstance(schema_skills, str):
            _add_all(s for s in re.split(r"[,;|]", schema_skills) if s.strip())

    # 2. Ô "Job function" trong job criteria của LinkedIn
    function = criteria.get("job function", "")
    if function:
        _add_all(s for s in re.split(r"[,;|]", function) if s.strip())

    # 3. Quét trong Yêu cầu + Mô tả bằng RAW_SKILL_PATTERNS
    _add_all(_scan_skills_in_text(requirements_raw))
    _add_all(_scan_skills_in_text(description_raw))

    return combined


# ============================================================
# CRAWL SINGLE JOB
# ============================================================

def crawl_job(
    card: dict[str, str],
    session: requests.Session | None = None,
) -> dict[str, Any]:
    """Tải trang chi tiết của MỘT job và ghép với dữ liệu đã có sẵn từ
    thẻ job ở trang kết quả (`card`: job_id, job_url, job_title,
    company_name, location, updated_at)."""
    session = session or make_session()
    job_id = card["job_id"]

    html = fetch_html(build_detail_url(job_id), session)
    soup = BeautifulSoup(html, "html.parser")
    schema = find_job_posting_json(soup)
    criteria = job_criteria(soup)

    title = clean_text(str(schema.get("title", ""))) or card.get("job_title", "")
    if not title:
        heading = soup.select_one("h1, h2.top-card-layout__title")
        if heading:
            title = clean_text(heading.get_text(" ", strip=True))

    organization = schema.get("hiringOrganization", {})
    company = (
        clean_text(str(organization.get("name", "")))
        if isinstance(organization, dict)
        else ""
    )
    if not company:
        node = soup.select_one("a.topcard__org-name-link, .topcard__flavor")
        if node:
            company = clean_text(node.get_text(" ", strip=True))
    if not company:
        company = card.get("company_name", "")

    full_text = extract_full_description(soup)
    description_raw, requirements_raw = split_description_requirements(full_text)

    skills_raw = extract_skills_raw(schema, criteria, requirements_raw, description_raw)

    # updated_at: ưu tiên <time datetime> ở thẻ job (đáng tin nhất cho
    # khách), dự phòng bằng datePosted trong JSON-LD.
    updated_at = card.get("updated_at", "") or clean_text(
        str(schema.get("datePosted", ""))
    )

    return {
        "job_id": f"{SOURCE_ID}_{job_id}",
        "source_id": SOURCE_ID,
        "job_title": title,
        "company_name": company,
        "level": extract_level(criteria, title),
        "salary_raw": extract_salary(soup, schema, full_text),
        "experience_raw": extract_experience(full_text, schema),
        "updated_at": updated_at,
        "deadline": clean_text(str(schema.get("validThrough", ""))),
        "location": extract_location(soup, schema, card.get("location", "")),
        "job_url": card.get("job_url") or canonical_job_url(job_id),
        "description_raw": description_raw,
        "requirements_raw": requirements_raw,
        "skills_raw": skills_raw,
        "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }


# ============================================================
# COLLECT JOB CARDS TỪ TRANG KẾT QUẢ
# ============================================================

def parse_job_cards(html: str) -> list[dict[str, str]]:
    """Bóc các thẻ job từ mảnh HTML của endpoint tìm kiếm. Mỗi thẻ đã
    có sẵn tiêu đề, công ty, địa điểm và NGÀY ĐĂNG — nhờ đó lọc được
    theo ngày TRƯỚC khi tốn request tải trang chi tiết."""
    soup = BeautifulSoup(html, "html.parser")
    cards: list[dict[str, str]] = []

    for node in soup.select("li"):
        base = node.select_one("[data-entity-urn]") or node.select_one(".base-card")
        job_id = ""
        if base is not None:
            job_id = extract_job_id(str(base.get("data-entity-urn", "")))

        link = node.select_one("a.base-card__full-link, a.base-search-card__title-link, a[href*='/jobs/view/']")
        href = link.get("href", "") if link else ""
        if not job_id:
            job_id = extract_job_id(str(href))
        if not job_id:
            continue

        title_node = node.select_one("h3.base-search-card__title, .sr-only")
        company_node = node.select_one(
            "h4.base-search-card__subtitle a, h4.base-search-card__subtitle"
        )
        location_node = node.select_one(".job-search-card__location")
        time_node = node.select_one("time")

        posted = ""
        if time_node is not None:
            posted = clean_text(str(time_node.get("datetime", "")))
            if not posted:
                posted = clean_text(time_node.get_text(" ", strip=True))

        cards.append(
            {
                "job_id": job_id,
                "job_url": canonical_job_url(job_id),
                "job_title": clean_text(title_node.get_text(" ", strip=True)) if title_node else "",
                "company_name": clean_text(company_node.get_text(" ", strip=True)) if company_node else "",
                "location": clean_text(location_node.get_text(" ", strip=True)) if location_node else "",
                "updated_at": posted,
            }
        )

    return cards


def collect_job_cards(
    keyword: str,
    session: requests.Session,
    *,
    location: str,
    geo_id: str,
    f_tpr: str | None,
    max_pages: int | None = None,
    limit: int | None = None,
    delay: float = 4.0,
    date_from: date | None = None,
) -> list[dict[str, str]]:
    """Duyệt phân trang kết quả cho MỘT từ khóa. Vì đã sortBy=DD (mới
    nhất trước), gặp liên tiếp nhiều job cũ hơn --from-date thì dừng
    sớm thay vì duyệt hết."""
    collected: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    start = 0
    page = 0
    stale_streak = 0

    while True:
        if max_pages is not None and page >= max_pages:
            break
        if limit is not None and len(collected) >= limit:
            break

        url = build_search_url(
            keyword, location=location, geo_id=geo_id, start=start, f_tpr=f_tpr
        )
        print(f"  [trang {page + 1}] {keyword} (start={start})", file=sys.stderr)

        try:
            html = fetch_html(url, session)
        except RateLimited as error:
            print(f"  [DỪNG] {error}", file=sys.stderr)
            break
        except requests.HTTPError as error:
            # 400 thường nghĩa là đã vượt quá số job LinkedIn chịu trả
            print(f"  [HẾT/ LỖI] {error}", file=sys.stderr)
            break

        cards = parse_job_cards(html)
        if not cards:
            break

        new_in_page = 0
        for card in cards:
            if card["job_id"] in seen_ids:
                continue
            seen_ids.add(card["job_id"])
            new_in_page += 1

            if date_from is not None:
                posted = parse_posted_date(card["updated_at"])
                if posted is not None and posted < date_from:
                    stale_streak += 1
                    continue

            stale_streak = 0
            collected.append(card)
            if limit is not None and len(collected) >= limit:
                break

        if new_in_page == 0:
            break
        if stale_streak >= 20:
            print(
                "  (dừng sớm: 20 job liên tiếp cũ hơn --from-date)",
                file=sys.stderr,
            )
            break

        start += 10
        page += 1
        time.sleep(delay + random.uniform(0, 1.5))

    return collected


# ============================================================
# SAVE / LOAD
# ============================================================

FIELDNAMES = [
    "job_id", "source_id", "job_title", "company_name", "level",
    "salary_raw", "experience_raw", "updated_at", "deadline",
    "location", "job_url", "description_raw", "requirements_raw",
    "skills_raw", "expertise_category", "crawled_at", "error",
]


def load_existing_results(output: Path) -> list[dict[str, Any]]:
    """Đọc các bản ghi đã crawl TRƯỚC ĐÓ (nếu file tồn tại). Dùng cho
    --append: chạy tiếp vào những ngày sau mà không mất dữ liệu cũ và
    không ghi trùng (dedup theo job_url ở nơi gọi)."""
    if not output.exists():
        return []
    with output.open("r", newline="", encoding="utf-8-sig") as file:
        return list(csv.DictReader(file))


def save_results(data: list[dict[str, Any]], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for record in data:
            row = record.copy()
            if isinstance(row.get("skills_raw"), list):
                row["skills_raw"] = " | ".join(row["skills_raw"])
            writer.writerow(row)


def crawl_one_job_safe(
    card: dict[str, str],
    session: requests.Session,
    category: str = "",
) -> dict[str, Any]:
    """Crawl 1 job, KHÔNG để exception làm dừng cả batch — lỗi ghi vào
    cột 'error' của chính record đó. RateLimited được ném lên trên để
    vòng ngoài quyết định dừng hẳn."""
    try:
        result = crawl_job(card, session)
        result["error"] = ""
        print(f"  ✓ {result['job_title']}", file=sys.stderr)
    except RateLimited:
        raise
    except Exception as error:
        print(f"  ✗ ERROR: {error}", file=sys.stderr)
        result = {
            "job_id": f"{SOURCE_ID}_{card.get('job_id', '')}",
            "source_id": SOURCE_ID,
            "job_title": card.get("job_title", ""),
            "company_name": card.get("company_name", ""),
            "level": "", "salary_raw": "", "experience_raw": "",
            "updated_at": card.get("updated_at", ""), "deadline": "",
            "location": card.get("location", ""),
            "job_url": card.get("job_url", ""),
            "description_raw": "", "requirements_raw": "",
            "skills_raw": [], "error": str(error),
            "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    result["expertise_category"] = category
    return result


# ============================================================
# CRAWL THEO DOMAIN
# ============================================================

def crawl_by_expertise(
    expertise_map: dict[str, list[str]],
    output: Path,
    *,
    location: str,
    geo_id: str,
    max_pages: int | None = None,
    limit: int | None = None,
    delay: float = 4.0,
    date_from: date | None = None,
    date_to: date | None = None,
    append: bool = False,
) -> list[dict[str, Any]]:
    session = make_session()
    f_tpr = derive_f_tpr(date_from)

    results: list[dict[str, Any]] = []
    seen_job_urls: set[str] = set()

    if append:
        results = load_existing_results(output)
        seen_job_urls = {r.get("job_url", "") for r in results if r.get("job_url")}
        print(
            f"[INFO] --append: đã nạp {len(results)} bản ghi có sẵn từ "
            f"{output.name}, sẽ bỏ qua các job đã crawl.",
            file=sys.stderr,
        )

    skipped_out_of_range = 0
    stopped = False

    for domain, keywords in expertise_map.items():
        if stopped:
            break

        print(f"\n{'=' * 60}\nDOMAIN: {domain}\n{'=' * 60}", file=sys.stderr)

        # 1. Gom thẻ job từ mọi từ khóa của domain, khử trùng lặp theo job_id
        domain_cards: list[dict[str, str]] = []
        domain_ids: set[str] = set()
        for keyword in keywords:
            remaining = None if limit is None else max(0, limit - len(domain_cards))
            if remaining == 0:
                break
            cards = collect_job_cards(
                keyword,
                session,
                location=location,
                geo_id=geo_id,
                f_tpr=f_tpr,
                max_pages=max_pages,
                limit=remaining,
                delay=delay,
                date_from=date_from,
            )
            for card in cards:
                if card["job_id"] not in domain_ids:
                    domain_ids.add(card["job_id"])
                    domain_cards.append(card)
            time.sleep(delay)

        print(f"[{domain}] tìm thấy {len(domain_cards)} job.", file=sys.stderr)

        # 2. Tải chi tiết từng job
        for index, card in enumerate(domain_cards, start=1):
            job_url = card["job_url"]

            if job_url in seen_job_urls:
                # Đã crawl ở domain khác hoặc lần chạy trước -> giữ
                # nhãn expertise_category của lần gặp ĐẦU TIÊN.
                print(f"  (bỏ qua, đã có): {job_url}", file=sys.stderr)
                continue

            seen_job_urls.add(job_url)

            print(
                f"[{domain} {index}/{len(domain_cards)}] {job_url}",
                file=sys.stderr,
            )

            try:
                result = crawl_one_job_safe(card, session, category=domain)
            except RateLimited as error:
                print(
                    f"\n[DỪNG] Bị LinkedIn chặn: {error}\n"
                    "Dữ liệu đã crawl vẫn được giữ trong file. Hãy đợi vài "
                    "giờ rồi chạy lại với --append để tiếp tục.",
                    file=sys.stderr,
                )
                stopped = True
                break

            if not is_within_date_range(result.get("updated_at", ""), date_from, date_to):
                skipped_out_of_range += 1
                print(
                    f"  (bỏ qua, ngoài khoảng ngày lọc: {result.get('updated_at')!r})",
                    file=sys.stderr,
                )
            else:
                results.append(result)
                save_results(results, output)

            if index < len(domain_cards):
                time.sleep(delay + random.uniform(0, 1.5))

    save_results(results, output)

    if date_from or date_to:
        print(
            f"[INFO] Đã lọc bỏ {skipped_out_of_range} job ngoài khoảng ngày (tổng).",
            file=sys.stderr,
        )

    return results


# ============================================================
# MAIN ENTRYPOINT
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Crawl Raw Job Data từ LinkedIn (Phase 1)",
        epilog=(
            'Ví dụ: python crawllinkedin.py --expertise "Frontend,DevOps/SRE" '
            "--from-date 17/09/2026 --to-date 30/09/2026 --limit 150 --append"
        ),
    )
    parser.add_argument(
        "-o", "--output", type=Path, default=Path("raw_jobs_linkedin.csv"),
        help="Output CSV Path",
    )
    parser.add_argument("--max-pages", type=int, help="Số trang tối đa mỗi từ khóa")
    parser.add_argument(
        "--limit", type=int,
        help="Số job tối đa. Khi dùng --expertise, limit áp dụng RIÊNG cho từng domain.",
    )
    parser.add_argument(
        "--delay", type=float, default=4.0,
        help="Delay giữa các request (giây). LinkedIn chặn mạnh, đừng để dưới 3.0.",
    )
    parser.add_argument(
        "--location", default=DEFAULT_LOCATION,
        help=f"Địa điểm tìm kiếm (mặc định: {DEFAULT_LOCATION})",
    )
    parser.add_argument(
        "--geo-id", default=DEFAULT_GEO_ID,
        help=(
            f"geoId của LinkedIn cho địa điểm (mặc định {DEFAULT_GEO_ID} = Vietnam). "
            "Lấy geoId bằng cách tìm job trên trình duyệt rồi đọc tham số geoId "
            "trên thanh địa chỉ. Truyền chuỗi rỗng để bỏ qua."
        ),
    )
    parser.add_argument(
        "--expertise", nargs="?", const="all", default="all",
        help=(
            "Chọn domain cần crawl. Dùng '--expertise' không kèm giá trị "
            "(hoặc 'all') để crawl HẾT. Muốn chọn NHIỀU domain, PHẢI để "
            "trong MỘT chuỗi có ngoặc kép, cách nhau bằng dấu PHẨY — không "
            'phải dấu cách, vd: --expertise "Frontend,Mobile". '
            f"Danh sách domain: {', '.join(EXPERTISE_QUERIES)}."
        ),
    )
    parser.add_argument(
        "--from-date", type=str, default=None,
        help=(
            "Chỉ giữ job có ngày đăng >= ngày này, định dạng DD/MM/YYYY "
            "(vd 01/07/2026). Script tự chốt f_TPR vào mốc chính thức gần "
            "nhất của LinkedIn (24h/tuần/tháng); quá 30 ngày trước hôm nay "
            "thì gần như không lấy lại được job cũ, kể cả với cờ này."
        ),
    )
    parser.add_argument(
        "--to-date", type=str, default=None,
        help="Chỉ giữ job có ngày đăng <= ngày này, định dạng DD/MM/YYYY (vd 30/09/2026)",
    )
    parser.add_argument(
        "--append", action="store_true",
        help=(
            "Nối kết quả vào file CSV đã có ở --output thay vì ghi đè. Tự động "
            "bỏ qua (không crawl lại, không ghi trùng) các job đã có sẵn dựa "
            "theo job_url — dùng để chạy tiếp vào những ngày sau."
        ),
    )

    args = parser.parse_args()

    def _parse_cli_date(value: str | None, flag_name: str) -> date | None:
        if value is None:
            return None
        try:
            return datetime.strptime(value, "%d/%m/%Y").date()
        except ValueError:
            print(
                f"[ERROR] {flag_name} phải theo định dạng DD/MM/YYYY, "
                f"nhận được: {value!r}",
                file=sys.stderr,
            )
            sys.exit(1)

    date_from = _parse_cli_date(args.from_date, "--from-date")
    date_to = _parse_cli_date(args.to_date, "--to-date")

    if date_from and date_to and date_from > date_to:
        print("[ERROR] --from-date phải nhỏ hơn hoặc bằng --to-date", file=sys.stderr)
        sys.exit(1)

    warn_date_range_reachable(date_from, date_to)

    if args.delay < 2.0:
        print(
            f"[CẢNH BÁO] --delay {args.delay}s quá thấp cho LinkedIn, rất dễ bị "
            "chặn IP. Khuyến nghị >= 3.0.",
            file=sys.stderr,
        )

    if args.expertise.strip().lower() == "all":
        expertise_map = EXPERTISE_QUERIES
    else:
        selected = [d.strip() for d in args.expertise.split(",") if d.strip()]
        expertise_map = {}
        for token in selected:
            key = resolve_domain_key(token)
            if key is None:
                print(
                    f"[WARNING] Domain '{token}' không nhận diện được, bỏ qua. "
                    f"Domain hỗ trợ: {list(EXPERTISE_QUERIES)}",
                    file=sys.stderr,
                )
                continue
            expertise_map[key] = EXPERTISE_QUERIES[key]

        if not expertise_map:
            print(
                "Không có domain hợp lệ nào được chọn. "
                f"Domain hỗ trợ: {list(EXPERTISE_QUERIES)}",
                file=sys.stderr,
            )
            sys.exit(1)

    results = crawl_by_expertise(
        expertise_map,
        args.output,
        location=args.location,
        geo_id=args.geo_id,
        max_pages=args.max_pages,
        limit=args.limit,
        delay=args.delay,
        date_from=date_from,
        date_to=date_to,
        append=args.append,
    )

    errors = sum(bool(item.get("error")) for item in results)
    print(
        f"\nHoàn tất: {len(results)} jobs trên {len(expertise_map)} domain, "
        f"{errors} lỗi."
    )
    print(f"File lưu tại: {args.output.resolve()}")


if __name__ == "__main__":
    main()