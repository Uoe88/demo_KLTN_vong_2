"""
ITviec Job Crawler - Phase 1: Raw Data Collection

Output CSV Fields:
- job_id
- source_id
- job_title
- company_name
- level
- salary_raw
- experience_raw
- updated_at
- deadline
- location
- job_url
- description_raw
- requirements_raw
- skills_raw 

"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import (
    parse_qs,
    parse_qsl,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://itviec.com"
SOURCE_ID = "ITV"
DEFAULT_URL = "https://itviec.com/viec-lam-it/java"
JOB_PATH_PATTERN = re.compile(r"^/(?:viec-lam-it|it-jobs)/.+-\d{4}/?$")

# Mapping domain -> URL(s) trang "Jobs by Expertise" của ITviec (đã xác
# minh trực tiếp qua itviec.com/jobs-expertise-index). Một domain có
# thể gộp nhiều expertise URL của ITviec (vd DevOps/SRE gồm 3 URL con)
# — script sẽ tự gộp + khử trùng lặp trong cùng domain.
EXPERTISE_URLS: dict[str, list[str]] = {
    "Backend": ["https://itviec.com/it-jobs/backend-developer"],
    "Frontend": ["https://itviec.com/it-jobs/frontend-developer"],
    "Fullstack": ["https://itviec.com/it-jobs/fullstack-developer"],
    "Data Engineer": ["https://itviec.com/it-jobs/data-engineer"],
    "Data Scientist": ["https://itviec.com/it-jobs/data-scientist"],
    "Data Analyst": ["https://itviec.com/it-jobs/data-analyst"],
    "AI Engineer": ["https://itviec.com/it-jobs/ai-machine-learning-engineer"],
    "DevOps/SRE": [
        "https://itviec.com/it-jobs/devops-engineer",
        "https://itviec.com/it-jobs/site-reliability-engineer-sre",
        "https://itviec.com/it-jobs/cloud-engineer",
    ],
    "PM/BA": [
        "https://itviec.com/it-jobs/project-manager",
        "https://itviec.com/it-jobs/business-analyst",
    ],
    "QA/Tester": [
        "https://itviec.com/it-jobs/manual-tester",
        "https://itviec.com/it-jobs/automation-tester",
    ],
    "Mobile": ["https://itviec.com/it-jobs/mobile-application-developer"],
    "Architect": [
        "https://itviec.com/it-jobs/software-technical-architect",
        "https://itviec.com/it-jobs/solution-architect",
    ],
    "Security": ["https://itviec.com/it-jobs/security-engineer"],
}

# Alias để nhận diện domain một cách linh hoạt: chấp nhận cả kiểu gõ
# thường ngày / slug quen thuộc trên URL, không phân biệt hoa-thường.
# Key ở đây LUÔN viết bằng chữ thường để so khớp case-insensitive.
DOMAIN_ALIASES: dict[str, str] = {
    "backend": "Backend",
    "backend-developer": "Backend",
    "be": "Backend",
    "frontend": "Frontend",
    "frontend-developer": "Frontend",
    "fe": "Frontend",
    "fullstack": "Fullstack",
    "fullstack-developer": "Fullstack",
    "full-stack": "Fullstack",
    "data engineer": "Data Engineer",
    "data-engineer": "Data Engineer",
    "de": "Data Engineer",
    "data scientist": "Data Scientist",
    "data-scientist": "Data Scientist",
    "ds": "Data Scientist",
    "data analyst": "Data Analyst",
    "data-analyst": "Data Analyst",
    "da": "Data Analyst",
    "ai": "AI Engineer",
    "ai engineer": "AI Engineer",
    "ai-engineer": "AI Engineer",
    "ai-machine-learning-engineer": "AI Engineer",
    "ml": "AI Engineer",
    "machine learning": "AI Engineer",
    "devops": "DevOps/SRE",
    "devops-engineer": "DevOps/SRE",
    "devops/sre": "DevOps/SRE",
    "sre": "DevOps/SRE",
    "site-reliability-engineer-sre": "DevOps/SRE",
    "cloud-engineer": "DevOps/SRE",
    "pm": "PM/BA",
    "ba": "PM/BA",
    "pm/ba": "PM/BA",
    "project-manager": "PM/BA",
    "business-analyst": "PM/BA",
    "qa": "QA/Tester",
    "qa/tester": "QA/Tester",
    "tester": "QA/Tester",
    "manual-tester": "QA/Tester",
    "automation-tester": "QA/Tester",
    "mobile": "Mobile",
    "mobile-application-developer": "Mobile",
    "architect": "Architect",
    "software-technical-architect": "Architect",
    "solution-architect": "Architect",
    "security": "Security",
    "security-engineer": "Security",
}


def resolve_domain_key(token: str) -> str | None:
    """Trả về đúng key trong EXPERTISE_URLS ứng với `token` người dùng
    gõ, chấp nhận: khớp chính xác, khác hoa-thường, hoặc alias/slug
    quen thuộc (vd 'backend-developer' -> 'Backend'). Trả None nếu
    không nhận diện được."""
    t = token.strip()
    if not t:
        return None

    # 1. Khớp chính xác (không phân biệt hoa-thường) với key gốc
    for key in EXPERTISE_URLS:
        if key.casefold() == t.casefold():
            return key

    # 2. Khớp qua bảng alias (không phân biệt hoa-thường)
    return DOMAIN_ALIASES.get(t.casefold())


# ============================================================
# SESSION
# ============================================================

def make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
        raise_on_status=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0 Safari/537.36"
            ),
            "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.8",
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
        }
    )
    return session


# ============================================================
# TEXT CLEANING & EXCEL ESCAPING
# ============================================================

def clean_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip()
    # Loại bỏ dấu '=' ở đầu để tránh lỗi #NAME? khi mở file CSV trong Excel
    if cleaned.startswith("="):
        cleaned = cleaned.lstrip("=")
    return cleaned.strip()


# ============================================================
# URL NORMALIZATION
# ============================================================

def normalize_job_url(url: str) -> str:
    parsed = urlparse(url)
    selected_job = parse_qs(parsed.query).get("job_selected", [None])[0]

    if selected_job:
        language_path = (
            "it-jobs" if parsed.path.startswith("/it-jobs") else "viec-lam-it"
        )
        return urlunparse(
            (
                parsed.scheme or "https",
                parsed.netloc or "itviec.com",
                f"/{language_path}/{selected_job}",
                "",
                "",
                "",
            )
        )

    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            "",
            "",
            "",
        )
    )


def is_job_detail_url(url: str) -> bool:
    return bool(JOB_PATH_PATTERN.match(urlparse(url).path))


def normalize_search_url(url: str) -> str:
    parsed = urlparse(url)
    tracking_keys = {
        "gclid",
        "gbraid",
        "gad_source",
        "gad_campaignid",
        "job_selected",
    }
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if (
            key not in tracking_keys
            and not key.startswith("utm_")
            and key != "page"
        )
    ]
    return urlunparse(
        (
            parsed.scheme or "https",
            parsed.netloc or "itviec.com",
            parsed.path,
            "",
            urlencode(query),
            "",
        )
    )


# ============================================================
# DATE FILTER — lọc job theo khoảng ngày đăng
# ============================================================

# Cụm relative-time dự phòng khi schema thiếu datePosted (schema.org
# JobPosting.datePosted BẮT BUỘC là ISO 8601 tuyệt đối để Google Jobs
# index được, nên đây chỉ là lưới an toàn phụ, không phải nguồn chính).
_RELATIVE_TIME_RE = re.compile(
    r"(\d+)\s*"
    r"(hour|hours|giờ|day|days|ngày|week|weeks|tuần|month|months|tháng)"
    r"\s*(ago|trước)",
    re.IGNORECASE,
)


def parse_posted_date(raw: str, reference: datetime | None = None) -> date | None:
    """Chuyển 'updated_at' (lấy từ schema datePosted, dạng ISO 8601)
    thành đối tượng date để so sánh khoảng lọc. Trả None nếu không
    parse được — job đó sẽ được GIỮ LẠI mặc định (an toàn hơn loại
    nhầm) thay vì bị âm thầm loại bỏ."""

    if not raw:
        return None

    text = raw.strip()

    # 1. ISO 8601 — nguồn chính (schema.org datePosted)
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
        else:  # month / tháng — ước lượng 30 ngày/tháng
            delta = timedelta(days=amount * 30)

        return (reference - delta).date()

    if "today" in text.casefold() or "hôm nay" in text.casefold():
        return reference.date()

    if "yesterday" in text.casefold() or "hôm qua" in text.casefold():
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
# DOM HELPER
# ============================================================

def values_after_label(soup: BeautifulSoup, labels: tuple[str, ...]) -> list[str]:
    wanted = {label.casefold().rstrip(":") for label in labels}

    for text_node in soup.find_all(string=True):
        parent = text_node.parent
        if not isinstance(parent, Tag) or parent.name in {"script", "style"}:
            continue

        label_text = clean_text(str(text_node)).casefold().rstrip(":")
        if label_text not in wanted:
            continue

        row = parent.parent
        if not isinstance(row, Tag):
            continue

        value_box = parent.find_next_sibling()
        if value_box is None:
            children = [child for child in row.children if isinstance(child, Tag)]
            if parent in children:
                position = children.index(parent)
                if position + 1 < len(children):
                    value_box = children[position + 1]

        if isinstance(value_box, Tag):
            values = [
                clean_text(tag.get_text(" ", strip=True))
                for tag in value_box.select(".itag, .badge-skill, .job-keyword")
            ]
            if not values:
                value = clean_text(value_box.get_text(" ", strip=True))
                values = [value] if value else []

            if values:
                return list(dict.fromkeys(values))

    return []


def find_section_text(soup: BeautifulSoup, labels: tuple[str, ...]) -> str:
    wanted = {label.casefold() for label in labels}

    for heading in soup.find_all(["h2", "h3", "h4"]):
        heading_text = clean_text(heading.get_text(" ", strip=True))
        if heading_text.casefold() not in wanted:
            continue

        parent = heading.parent
        if not isinstance(parent, Tag):
            continue

        text = clean_text(parent.get_text("\n", strip=True))
        if text:
            text = re.sub(re.escape(heading_text), "", text, count=1, flags=re.I)
            return clean_text(text)

    return ""


# ============================================================
# FIELD EXTRACTION
# ============================================================

def extract_description(soup: BeautifulSoup) -> str:
    return find_section_text(
        soup, ("Mô tả công việc", "Job description", "Description")
    )


def extract_requirements(soup: BeautifulSoup) -> str:
    return find_section_text(
        soup,
        (
            "Yêu cầu công việc",  # Thêm tiêu đề trong JD của bạn
            "Yêu cầu ứng viên",
            "Yêu cầu",
            "Requirements",
            "Job requirements",
            "Requirements & skills",
            "Must have",           # Thêm nhãn tiếng Anh phổ biến
        ),
    )


# Các cụm từ báo hiệu "phải đăng nhập mới xem được lương" — ITviec khóa
# lương với nhiều job, và selector .salary/.job-salary đôi khi vô tình
# bắt trúng đúng dòng thông báo này thay vì mức lương thật.
_SALARY_LOCK_PHRASES = (
    "đăng nhập",
    "dang nhap",
    "login to view",
    "sign in to view",
    "log in to view",
)


def _is_salary_locked(text: str) -> bool:
    t = text.casefold()
    return any(phrase in t for phrase in _SALARY_LOCK_PHRASES)


def extract_salary(soup: BeautifulSoup, job_schema: dict[str, Any]) -> str:
    for use_tag in soup.select('use[href$="#currency-dollar"], use[href$="#dollar"]'):
        container = use_tag.find_parent(["div", "span"])
        if container:
            val = clean_text(container.get_text(" ", strip=True))
            # Bỏ qua nếu đây là dòng "Đăng nhập để xem mức lương" thay vì
            # mức lương thật — thử tiếp các nguồn khác thay vì trả về
            # nguyên văn thông báo khóa lương.
            if val and not _is_salary_locked(val):
                return val

    salary_tag = soup.select_one(".salary, .job-salary, .text-salary")
    if salary_tag:
        val = clean_text(salary_tag.get_text(" ", strip=True))
        if val and not _is_salary_locked(val):
            return val

    base_salary = job_schema.get("baseSalary")
    if isinstance(base_salary, dict):
        value = base_salary.get("value", {})
        currency = base_salary.get("currency", "")
        if isinstance(value, dict):
            min_val = value.get("minValue")
            max_val = value.get("maxValue")
            unit = value.get("unitText", "")
            if min_val and max_val:
                return f"{min_val} - {max_val} {currency}/{unit}".strip()
            elif min_val:
                return f"From {min_val} {currency}/{unit}".strip()
        elif isinstance(value, (int, float, str)):
            val = f"{value} {currency}".strip()
            if val and not _is_salary_locked(val):
                return val

    # Không tìm được mức lương thật ở bất kỳ nguồn nào (job này khóa
    # lương sau đăng nhập) -> để trống thay vì lưu thông báo khóa lương.
    return ""


def extract_experience(soup: BeautifulSoup, description_raw: str = "") -> str:
    """Extract experience requirement from UI labels, relevant sections, or raw description."""
    
    # 1. UI Labels
    labels = ("Kinh nghiệm", "Experience", "Min experience", "Minimum experience", "Kinh nghiệm tối thiểu", "Years of experience")
    if exp_list := values_after_label(soup, labels):
        if cleaned := [clean_text(x) for x in exp_list if clean_text(x)]:
            return " | ".join(cleaned)

    # 2. Extract sections under relevant headings
    headings_kw = (
        "your skills and experience", "skills and experience", "skills & experience", 
        "experience and skills", "requirements", "requirement", "qualifications", 
        "job requirements", "must", "must have", "must-have", "must haves", "must-haves", 
        "what you need", "what we're looking for",
        "yêu cầu công việc", "yêu cầu ứng viên", "yêu cầu", "kinh nghiệm",
    )
    
    section_texts = []
    if soup:
        for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"]):
            h_text = clean_text(heading.get_text(" ", strip=True)).lower()
            if h_text and any(h_text == h or h_text.startswith((f"{h}:", f"{h} ")) for h in headings_kw):
                parts = []
                for curr in heading.find_next_siblings():
                    if curr.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                        break
                    if txt := clean_text(curr.get_text(" ", strip=True)):
                        parts.append(txt)
                if parts:
                    section_texts.append("\n".join(parts))

    if description_raw:
        section_texts.append(description_raw)

    search_text = "\n".join(filter(None, section_texts))
    if not search_text:
        return ""

    # 3. Combined Regex Matching
    patterns = [
        r"\b\d+\s*(?:[-–—]\s*\d+\s*)?\+?\s*(?:years?|yrs?)(?:\s+of)?\s+experience\b",
        r"\b\d+\s*(?:[-–—]\s*\d+\s*)?năm\s+kinh\s*nghiệm\b",
        r"\b\d+\s*(?:[-–—]\s*\d+\s*)?\+?\s*(?:years?|yrs?)\b",
        r"\b\d+\s*(?:[-–—]\s*\d+\s*)?năm\b"
    ]

    for pattern in patterns:
        if match := re.search(pattern, search_text, re.IGNORECASE):
            if result := clean_text(match.group(0)):
                return result

    return ""


def find_location(soup: BeautifulSoup, job_schema: dict[str, Any]) -> str:
    for use_tag in soup.select('use[href$="#map-pin"]'):
        container = use_tag.find_parent("div")
        if container:
            address = container.select_one("span")
            if address:
                value = clean_text(address.get_text(" ", strip=True))
                if value:
                    return value

    locations = job_schema.get("jobLocation", [])
    if isinstance(locations, dict):
        locations = [locations]

    for location in locations:
        if not isinstance(location, dict):
            continue
        address = location.get("address", {})
        if not isinstance(address, dict):
            continue

        parts = [
            address.get("streetAddress"),
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        ]
        unique_parts = list(
            dict.fromkeys(clean_text(str(part)) for part in parts if part)
        )
        if unique_parts:
            return ", ".join(unique_parts)

    return ""


def extract_level(soup: BeautifulSoup, job_schema: dict[str, Any]) -> str:
    levels = values_after_label(soup, ("Cấp bậc", "Level", "Job level"))
    if levels:
        return " | ".join(levels)

    title = clean_text(str(job_schema.get("title", "")))
    title_lower = title.lower()

    found = []
    level_mapping = {
        "intern": "Intern",
        "fresher": "Fresher",
        "junior": "Junior",
        "middle": "Middle",
        "mid-level": "Middle",
        "senior": "Senior",
        "lead": "Lead",
        "manager": "Manager",
    }
    for keyword, lvl in level_mapping.items():
        if keyword in title_lower:
            found.append(lvl)

    return " | ".join(list(dict.fromkeys(found))) if found else ""


# ============================================================
# RAW SKILL EXTRACTION (PHASE 1 - KHÔNG CHUẨN HÓA)
# ============================================================

# Danh mục pattern quét trong text Mô tả/Yêu cầu công việc. Đây LÀ nơi
# bắt được các skill cụ thể (vd "SQL Server", "MySQL", "PostgreSQL")
# mà phần badge/tag kỹ năng trên giao diện thường chỉ ghi chung chung
# (vd "Database", "SQL"). Vì vậy kết quả cuối = HỢP (union) của badge +
# schema + các skill match được trong text, KHÔNG dừng lại ở badge.
RAW_SKILL_PATTERNS = [
    # --- Ngôn ngữ lập trình ---
    r"\bTypeScript\b", r"\bJavaScript\b", r"\bNode\.js\b",
    r"\bObjective-C\b", r"\bC\+\+\b", r"\bC#\b", r"\b\.NET\b",
    r"\bJava\b", r"\bKotlin\b", r"\bScala\b", r"\bGroovy\b",
    r"\bPython\b", r"\bGo(lang)?\b", r"\bRust\b", r"\bRuby\b", r"\bPHP\b",
    r"\bSwift\b", r"\bDart\b",

    # --- Backend framework ---
    r"\bSpring Cloud\b", r"\bSpring Boot\b", r"\bSpring\b",
    r"\bRuby on Rails\b", r"\bASP\.NET( Core)?\b",
    r"\bDjango\b", r"\bFlask\b", r"\bFastAPI\b",
    r"\bExpress(\.js)?\b", r"\bNestJS\b",
    r"\bLaravel\b", r"\bSymfony\b", r"\bGin\b", r"\bEcho\b",

    # --- Frontend framework ---
    r"\bReact Native\b", r"\bNext\.js\b", r"\bNuxt(\.js)?\b", r"\bReact\b",
    r"\bVue\b", r"\bAngular\b", r"\bSvelte\b", r"\bRemix\b",
    r"\bTailwind( ?CSS)?\b", r"\bBootstrap\b",

    # --- Mobile ---
    r"\bJetpack Compose\b", r"\bFlutter\b", r"\bAndroid\b", r"\biOS\b", r"\bXcode\b",

    # --- CSDL / Database ---
    # Lưu ý: "\s*" giữa Postgre/SQL Server để bắt được cả kiểu viết rời
    # ("Postgre SQL", "SQL  Server") lẫn viết liền ("PostgreSQL").
    r"\bPostgre\s*SQL\b", r"\bPostgres\b",
    r"\bSQL\s*Server\b", r"\bMariaDB\b",
    r"\bMySQL\b", r"\bOracle\b", r"\bSQLite\b", r"\bMS\s*SQL\b", r"\bT-SQL\b", r"\bSQL\b",
    r"\bMongoDB\b", r"\bCassandra\b", r"\bDynamoDB\b",
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

    # --- Cloud / Hạ tầng ---
    r"\bGoogle Cloud\b", r"\bAlibaba Cloud\b", r"\bAWS\b", r"\bAzure\b", r"\bGCP\b",
    r"\bFirebase\b", r"\bSupabase\b", r"\bHeroku\b", r"\bVercel\b", r"\bNetlify\b",
    r"\bLambda\b", r"\bEC2\b", r"\bS3\b", r"\bECS\b", r"\bEKS\b",

    # --- DevOps / CI-CD ---
    r"\bGitHub Actions\b", r"\bGitLab CI\b", r"\bCircleCI\b",
    r"\bCI/CD\b", r"\bKubernetes\b", r"\bK8s\b", r"\bDocker\b",
    r"\bTerraform\b", r"\bAnsible\b", r"\bJenkins\b",
    r"\bArgoCD\b", r"\bHelm\b", r"\bNginx\b", r"\bApache\b",

    # --- Version control / Công cụ ---
    r"\bGitHub\b", r"\bGitLab\b", r"\bBitbucket\b", r"\bGit\b",
    r"\bJira\b", r"\bConfluence\b", r"\bPostman\b", r"\bSwagger\b", r"\bOpenAPI\b",

    # --- Testing ---
    r"\bPlaywright\b", r"\bSelenium\b", r"\bCypress\b",
    r"\bJUnit\b", r"\bMockito\b", r"\bPytest\b", r"\bJest\b", r"\bTestNG\b", r"\bK6\b",

    # --- Build tool ---
    r"\bWebpack\b", r"\bGradle\b", r"\bMaven\b", r"\bVite\b",
    r"\bnpm\b", r"\byarn\b", r"\bpnpm\b",

    # --- AI / Dev tools (AI-assisted coding) ---
    r"\bClaude Code\b", r"\bClaude\b", r"\bCursor\b", r"\bCopilot\b",
    r"\bChatGPT\b", r"\bGemini\b", r"\bWindsurf\b",

    # --- Bảo mật ---
    r"\bOAuth2?\b", r"\bKeycloak\b", r"\bJWT\b", r"\bSSO\b", r"\bSAML\b", r"\bOKTA\b",

    # --- Big Data / Data Engineering ---
    r"\bAirflow\b", r"\bHadoop\b", r"\bSpark\b", r"\bFlink\b", r"\bDbt\b", r"\bETL\b",
]


def _scan_skills_in_text(text: str) -> list[str]:
    """Quét 1 đoạn text bằng RAW_SKILL_PATTERNS, giữ NGUYÊN VĂN cách viết
    hoa/thường xuất hiện trong JD (không chuẩn hóa)."""
    found = []
    if not text:
        return found
    for pattern in RAW_SKILL_PATTERNS:
        for match in re.finditer(pattern, text, re.IGNORECASE):
            exact_word = match.group(0)
            if exact_word.casefold() not in {f.casefold() for f in found}:
                found.append(exact_word)
    return found


def extract_skills_raw(
    soup: BeautifulSoup,
    schema: dict[str, Any],
    requirements_raw: str,
    description_raw: str = "",
) -> list[str]:
    """
    Trích xuất từ khóa kỹ năng dạng RAW DATA (nguyên bản từ JD).
    Không gộp/không đổi tên để phục vụ bước Data Cleaning & Taxonomy (Phase 2).

    QUAN TRỌNG: trả về HỢP (union) của cả 3 nguồn dưới đây, KHÔNG dừng
    lại ở nguồn đầu tiên tìm thấy — vì phần badge/tag trên giao diện
    thường chỉ ghi skill chung chung (vd "Database"), trong khi phần
    Yêu cầu công việc mới ghi cụ thể (vd "SQL Server", "MySQL",
    "PostgreSQL"). Bỏ qua bất kỳ nguồn nào sẽ làm mất skill cụ thể.
    """
    combined: list[str] = []

    def _add_all(items):
        seen_lower = {s.casefold() for s in combined}
        for item in items:
            item = clean_text(str(item))
            if item and item.casefold() not in seen_lower:
                combined.append(item)
                seen_lower.add(item.casefold())

    # 1. Badge/Tag hiển thị trực tiếp trên giao diện
    _add_all(values_after_label(soup, ("Kỹ năng", "Skills", "Technical skills")))

    # 2. JSON-LD Schema
    schema_skills = schema.get("skills")
    if schema_skills:
        if isinstance(schema_skills, list):
            _add_all(str(s) for s in schema_skills if str(s).strip())
        elif isinstance(schema_skills, str):
            _add_all(s for s in re.split(r"[,;|]", schema_skills) if s.strip())

    # 3. Quét trong Mô tả công việc + Yêu cầu công việc bằng RAW_SKILL_PATTERNS
    #    (đây là nguồn bắt được các skill cụ thể mà badge có thể bỏ sót)
    _add_all(_scan_skills_in_text(requirements_raw))
    _add_all(_scan_skills_in_text(description_raw))

    return combined


# ============================================================
# CRAWL SINGLE JOB
# ============================================================

def crawl_job(url: str, session: requests.Session | None = None) -> dict[str, Any]:
    session = session or make_session()
    job_url = normalize_job_url(url)

    response = session.get(job_url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
    schema = find_job_posting_json(soup)

    title = clean_text(str(schema.get("title", "")))
    if not title:
        heading = soup.find("h1")
        if heading:
            title = clean_text(heading.get_text(" ", strip=True))

    organization = schema.get("hiringOrganization", {})
    company = (
        clean_text(str(organization.get("name", "")))
        if isinstance(organization, dict)
        else ""
    )

    description_raw = extract_description(soup)
    requirements_raw = extract_requirements(soup)

    # Trích xuất RAW skills: HỢP của badge + schema + quét text mô tả/yêu cầu
    skills_raw = extract_skills_raw(soup, schema, requirements_raw, description_raw)

    level = extract_level(soup, schema)
    salary_raw = extract_salary(soup, schema)
    experience_raw = extract_experience(soup, f"{description_raw}\n{requirements_raw}")
    updated_at = clean_text(str(schema.get("datePosted", "")))
    deadline = clean_text(str(schema.get("validThrough", "")))
    location = find_location(soup, schema)

    # Ngày/giờ THỰC TẾ script crawl được job này (khác với `updated_at`
    # là ngày job được ĐĂNG trên ITviec). Dùng để biết dữ liệu được
    # thu thập lúc nào, và làm mốc khi crawl nối tiếp các ngày sau.
    crawled_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return {
        "job_id": f"{SOURCE_ID}_{re.search(r'-(\d{4})$', urlparse(job_url).path.rstrip('/')).group(1)}" if re.search(r'-(\d{4})$', urlparse(job_url).path.rstrip('/')) else "",
        "source_id": SOURCE_ID,
        "job_title": title,
        "company_name": company,
        "level": level,
        "salary_raw": salary_raw,
        "experience_raw": experience_raw,
        "updated_at": updated_at,
        "deadline": deadline,
        "location": location,
        "job_url": job_url,
        "description_raw": description_raw,
        "requirements_raw": requirements_raw,
        "skills_raw": skills_raw,
        "crawled_at": crawled_at,
    }


# ============================================================
# CRAWL SEARCH & SAVE
# ============================================================

def extract_job_links(soup: BeautifulSoup, page_url: str) -> list[str]:
    links = []
    for tag in soup.select('a[href*="lab_feature=preview_jd_page"]'):
        href = tag.get("href")
        if isinstance(href, str):
            job_url = normalize_job_url(urljoin(page_url, href))
            if is_job_detail_url(job_url) and job_url not in links:
                links.append(job_url)
    return links


def collect_job_urls(
    search_url: str,
    session: requests.Session,
    *,
    max_pages: int | None = None,
    delay: float = 2.0,
) -> list[str]:
    page_url = normalize_search_url(search_url)
    visited_pages = set()
    job_urls = []
    page_number = 0

    while page_url and page_url not in visited_pages:
        if max_pages is not None and page_number >= max_pages:
            break

        visited_pages.add(page_url)
        page_number += 1
        print(f"[INFO] Crawling page {page_number}: {page_url}", file=sys.stderr)

        response = session.get(page_url, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "html.parser")
        page_jobs = extract_job_links(soup, response.url)

        if not page_jobs:
            break

        for job_url in page_jobs:
            if job_url not in job_urls:
                job_urls.append(job_url)

        print(f"[INFO] Page {page_number}: {len(page_jobs)} jobs | Total: {len(job_urls)}", file=sys.stderr)

        next_tag = soup.select_one('a[rel~="next"][href]')
        page_url = urljoin(response.url, next_tag["href"]) if next_tag else None

        if page_url:
            time.sleep(delay)

    return job_urls


def load_existing_results(output: Path) -> list[dict[str, Any]]:
    """Đọc các bản ghi đã crawl TRƯỚC ĐÓ từ file CSV output (nếu đã tồn
    tại). Dùng cho chế độ --append: cho phép chạy script vào những
    ngày tiếp theo mà KHÔNG mất dữ liệu cũ, và (kết hợp với dedup theo
    job_url ở nơi gọi) không crawl/ghi trùng lại các job đã có sẵn
    trong file. Trả về [] nếu file chưa tồn tại."""
    if not output.exists():
        return []
    with output.open("r", newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        return list(reader)


def save_results(data: list[dict[str, Any]], output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "job_id", "source_id", "job_title", "company_name", "level",
        "salary_raw", "experience_raw", "updated_at", "deadline",
        "location", "job_url", "description_raw", "requirements_raw",
        "skills_raw", "expertise_category", "crawled_at", "error",
    ]

    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in data:
            row = record.copy()
            if isinstance(row.get("skills_raw"), list):
                row["skills_raw"] = " | ".join(row["skills_raw"])
            writer.writerow(row)


def crawl_one_job_safe(
    job_url: str, session: requests.Session, category: str = ""
) -> dict[str, Any]:
    """Crawl 1 job, KHÔNG để exception văng lên làm dừng cả batch — lỗi
    được ghi vào cột 'error' của chính record đó. Dùng chung cho cả
    chế độ crawl 1 URL (crawl_all_jobs) và chế độ nhiều expertise
    (crawl_by_expertise), category được gắn vào expertise_category."""
    try:
        result = crawl_job(job_url, session)
        result["error"] = ""
        print(f"  ✓ {result['job_title']}", file=sys.stderr)
    except Exception as error:
        print(f"  ✗ ERROR: {error}", file=sys.stderr)
        result = {
            "job_id": "", "source_id": SOURCE_ID, "job_title": "",
            "company_name": "", "level": "", "salary_raw": "",
            "experience_raw": "", "updated_at": "", "deadline": "",
            "location": "", "job_url": job_url, "description_raw": "",
            "requirements_raw": "", "skills_raw": [], "error": str(error),
            "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }

    result["expertise_category"] = category
    return result


def crawl_all_jobs(
    search_url: str,
    output: Path,
    *,
    max_pages: int | None = None,
    limit: int | None = None,
    delay: float = 2.0,
    category: str = "",
    date_from: date | None = None,
    date_to: date | None = None,
    append: bool = False,
):
    session = make_session()
    job_urls = collect_job_urls(search_url, session, max_pages=max_pages, delay=delay)

    # Chế độ --append: nạp lại các job đã crawl ở lần chạy TRƯỚC (từ
    # chính file output), rồi loại các job đó khỏi danh sách sắp crawl
    # -> chỉ crawl job MỚI, không tốn request lại cho job cũ, và không
    # ghi trùng bản ghi trong CSV.
    existing_results: list[dict[str, Any]] = []
    if append:
        existing_results = load_existing_results(output)
        existing_urls = {r.get("job_url", "") for r in existing_results if r.get("job_url")}
        before = len(job_urls)
        job_urls = [u for u in job_urls if u not in existing_urls]
        print(
            f"[INFO] --append: {len(existing_results)} bản ghi cũ trong "
            f"{output.name}, bỏ qua {before - len(job_urls)} job đã có, "
            f"còn {len(job_urls)} job MỚI cần crawl.",
            file=sys.stderr,
        )

    if limit is not None:
        job_urls = job_urls[:limit]

    results = list(existing_results)
    skipped_out_of_range = 0
    for index, job_url in enumerate(job_urls, start=1):
        print(f"\n[{index}/{len(job_urls)}] {job_url}", file=sys.stderr)
        result = crawl_one_job_safe(job_url, session, category=category)

        if not is_within_date_range(result.get("updated_at", ""), date_from, date_to):
            skipped_out_of_range += 1
            print(
                f"  (bỏ qua, ngoài khoảng ngày lọc: {result.get('updated_at')!r})",
                file=sys.stderr,
            )
        else:
            results.append(result)
            save_results(results, output)

        if index < len(job_urls):
            time.sleep(delay)

    if date_from or date_to:
        print(
            f"[INFO] Đã lọc bỏ {skipped_out_of_range} job ngoài khoảng ngày.",
            file=sys.stderr,
        )

    return results


def crawl_by_expertise(
    expertise_map: dict[str, list[str]],
    output: Path,
    *,
    max_pages: int | None = None,
    limit: int | None = None,
    delay: float = 2.0,
    date_from: date | None = None,
    date_to: date | None = None,
    append: bool = False,
) -> list[dict[str, Any]]:
    """Crawl NHIỀU expertise (domain) trong 1 lần chạy, tự gắn nhãn
    expertise_category cho mỗi job — nhãn này là ground-truth domain
    lấy từ chính expertise của ITviec, hữu ích để đối chiếu độ chính
    xác của bước phân loại domain tự động sau này.

    `limit` áp dụng RIÊNG cho từng domain (không phải tổng toàn bộ),
    khớp với khuyến nghị cỡ mẫu tối thiểu ~50-80 job/domain đã bàn
    trước đó — vd --limit 80 nghĩa là tối đa 80 job MỖI domain.

    `date_from`/`date_to`: chỉ giữ job có ngày đăng (updated_at, lấy từ
    schema datePosted) nằm trong khoảng — job không parse được ngày
    vẫn được GIỮ LẠI mặc định (an toàn hơn loại nhầm).
    """
    session = make_session()

    # Chế độ --append: nạp lại bản ghi đã crawl ở lần chạy TRƯỚC, dùng
    # làm điểm khởi đầu cho `results` (để save_results ghi ra file gồm
    # CẢ dữ liệu cũ + mới) và cho `seen_job_urls` (để cơ chế dedup
    # trong-vòng-lặp có sẵn bên dưới tự động bỏ qua luôn các job đã
    # crawl ở NHỮNG NGÀY TRƯỚC, không chỉ trong cùng 1 lần chạy).
    existing_results: list[dict[str, Any]] = load_existing_results(output) if append else []
    results: list[dict[str, Any]] = list(existing_results)
    seen_job_urls: set[str] = {
        r.get("job_url", "") for r in existing_results if r.get("job_url")
    }
    if append and existing_results:
        print(
            f"[INFO] --append: đã nạp {len(existing_results)} bản ghi cũ "
            f"từ {output.name}, sẽ bỏ qua các job đã có.",
            file=sys.stderr,
        )
    skipped_out_of_range = 0

    for domain, search_urls in expertise_map.items():

        print(f"\n{'=' * 60}\nExpertise: {domain}\n{'=' * 60}", file=sys.stderr)

        domain_job_urls: list[str] = []
        for search_url in search_urls:
            found = collect_job_urls(
                search_url, session, max_pages=max_pages, delay=delay
            )
            for u in found:
                if u not in domain_job_urls:
                    domain_job_urls.append(u)

        # Loại bỏ job đã có sẵn (từ lần chạy trước, khi --append, hoặc
        # domain khác trong CÙNG lần chạy này) TRƯỚC khi áp limit, để
        # limit phản ánh đúng số job MỚI sẽ crawl cho domain này.
        domain_job_urls = [u for u in domain_job_urls if u not in seen_job_urls]

        if limit is not None:
            domain_job_urls = domain_job_urls[:limit]

        print(
            f"[INFO] {domain}: {len(domain_job_urls)} job URL MỚI cần crawl (sau khi áp limit)",
            file=sys.stderr,
        )

        for index, job_url in enumerate(domain_job_urls, start=1):

            if job_url in seen_job_urls:
                # ITviec có thể liệt kê 1 job dưới nhiều expertise cùng
                # lúc — bỏ qua để tránh trùng record, giữ nhãn domain
                # của lần crawl ĐẦU TIÊN gặp job này.
                print(
                    f"  (bỏ qua, đã crawl ở domain khác): {job_url}",
                    file=sys.stderr,
                )
                continue

            seen_job_urls.add(job_url)

            print(
                f"[{domain} {index}/{len(domain_job_urls)}] {job_url}",
                file=sys.stderr,
            )

            result = crawl_one_job_safe(job_url, session, category=domain)

            if not is_within_date_range(result.get("updated_at", ""), date_from, date_to):
                skipped_out_of_range += 1
                print(
                    f"  (bỏ qua, ngoài khoảng ngày lọc: {result.get('updated_at')!r})",
                    file=sys.stderr,
                )
            else:
                results.append(result)
                save_results(results, output)

            if index < len(domain_job_urls):
                time.sleep(delay)

    if date_from or date_to:
        print(
            f"[INFO] Đã lọc bỏ {skipped_out_of_range} job ngoài khoảng ngày (tổng).",
            file=sys.stderr,
        )

    return results


# ============================================================
# MAIN ENTRYPOINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Crawl Raw Job Data từ ITviec (Phase 1)")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="URL search hoặc URL job detail")
    parser.add_argument("-o", "--output", type=Path, default=Path("raw_jobs.csv"), help="Output CSV Path")
    parser.add_argument("--max-pages", type=int, help="Số trang tối đa")
    parser.add_argument(
        "--limit", type=int,
        help=(
            "Số job tối đa (vd: --limit 150 để thu 100-200 job). "
            "Khi dùng cùng --expertise, limit áp dụng RIÊNG cho từng domain."
        ),
    )
    parser.add_argument("--delay", type=float, default=2.0, help="Delay giữa các request")
    parser.add_argument(
        "--expertise", nargs="?", const="all", default=None,
        help=(
            "Crawl NHIỀU expertise (domain) trong 1 lần chạy thay vì 1 URL "
            "đơn lẻ, tự gắn nhãn expertise_category cho mỗi job. "
            "Dùng '--expertise' KHÔNG kèm giá trị (hoặc '--expertise all') "
            "để crawl HẾT các domain bên dưới. Muốn chọn NHIỀU domain, "
            "PHẢI để trong MỘT chuỗi có ngoặc kép, cách nhau bằng dấu "
            'phẩy — KHÔNG cách nhau bằng dấu cách, vd: '
            '--expertise "Backend,Frontend,Data Engineer" '
            '(không phải: --expertise Backend Frontend). '
            "Chấp nhận cả tên domain viết hoa lẫn slug quen thuộc trên "
            "URL (vd 'backend-developer', 'devops', không phân biệt "
            "hoa-thường). "
            f"Danh sách domain: {', '.join(EXPERTISE_URLS)}. "
            "Khi dùng cờ này, đối số 'url' vị trí bị bỏ qua."
        ),
    )
    parser.add_argument(
        "--from-date", type=str, default=None,
        help="Chỉ giữ job có ngày đăng >= ngày này, định dạng DD/MM/YYYY (vd 01/07/2026)",
    )
    parser.add_argument(
        "--to-date", type=str, default=None,
        help="Chỉ giữ job có ngày đăng <= ngày này, định dạng DD/MM/YYYY (vd 30/09/2026)",
    )
    parser.add_argument(
        "--append", action="store_true",
        help=(
            "Nối kết quả vào file CSV đã có ở --output (nếu tồn tại) thay vì "
            "ghi đè. Tự động bỏ qua (không crawl lại, không ghi trùng) các "
            "job đã có sẵn trong file, dựa theo job_url — dùng để crawl "
            "tiếp vào những ngày sau mà không bị trùng lặp bản ghi."
        ),
    )

    args = parser.parse_args()

    # --- Bẫy lỗi thường gặp: gõ nhiều domain cách nhau bằng dấu CÁCH
    # thay vì dấu phẩy trong 1 chuỗi. Vì `url` (positional) và
    # `--expertise` đều nargs="?", argparse sẽ ÂM THẦM nuốt domain thứ
    # 2 trở đi vào `url` mà không báo lỗi -> phát hiện và cảnh báo. ---
    if args.expertise is not None and args.url != DEFAULT_URL:
        print(
            "[CẢNH BÁO] Phát hiện đối số 'url' bị lệch khỏi giá trị mặc định "
            f"trong khi đang dùng --expertise: url={args.url!r}. "
            "Đây thường là do gõ NHIỀU domain cách nhau bằng dấu CÁCH "
            "(vd '--expertise Backend Frontend'), khiến 'Frontend' bị "
            "nuốt nhầm vào url. Hãy dùng dấu PHẨY trong một chuỗi có "
            'ngoặc kép, vd: --expertise "Backend,Frontend"',
            file=sys.stderr,
        )
        sys.exit(1)

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

    if args.expertise:
        if args.expertise.strip().lower() == "all":
            expertise_map = EXPERTISE_URLS
        else:
            selected = [d.strip() for d in args.expertise.split(",") if d.strip()]
            expertise_map = {}
            for d in selected:
                key = resolve_domain_key(d)
                if key is None:
                    print(
                        f"[WARNING] Domain '{d}' không nhận diện được, bỏ qua. "
                        f"Domain hỗ trợ: {list(EXPERTISE_URLS)}",
                        file=sys.stderr,
                    )
                    continue
                expertise_map[key] = EXPERTISE_URLS[key]

            if not expertise_map:
                print(
                    "Không có domain hợp lệ nào được chọn. "
                    f"Domain hỗ trợ: {list(EXPERTISE_URLS)}",
                    file=sys.stderr,
                )
                sys.exit(1)

        results = crawl_by_expertise(
            expertise_map,
            args.output,
            max_pages=args.max_pages,
            limit=args.limit,
            delay=args.delay,
            date_from=date_from,
            date_to=date_to,
            append=args.append,
        )
        errors = sum(bool(item.get("error")) for item in results)
        print(
            f"\nHoàn tất: {len(results)} jobs trên {len(expertise_map)} "
            f"domain, {errors} lỗi."
        )
        print(f"File lưu tại: {args.output.resolve()}")

    elif is_job_detail_url(normalize_job_url(args.url)):
        session = make_session()
        result = crawl_job(args.url, session)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        if args.append:
            existing = load_existing_results(args.output)
            existing_urls = {r.get("job_url", "") for r in existing if r.get("job_url")}
            if result["job_url"] in existing_urls:
                print(
                    f"[INFO] --append: job đã có sẵn trong {args.output.name}, bỏ qua ghi trùng.",
                    file=sys.stderr,
                )
                save_results(existing, args.output)
            else:
                save_results(existing + [result], args.output)
        else:
            save_results([result], args.output)

    else:
        results = crawl_all_jobs(
            args.url,
            args.output,
            max_pages=args.max_pages,
            limit=args.limit,
            delay=args.delay,
            date_from=date_from,
            date_to=date_to,
            append=args.append,
        )
        errors = sum(bool(item.get("error")) for item in results)
        print(f"\nHoàn tất: {len(results)} jobs, {errors} lỗi.")
        print(f"File lưu tại: {args.output.resolve()}")


if __name__ == "__main__":
    main()