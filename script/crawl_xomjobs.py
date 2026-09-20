"""
xomjobs
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Đổi số này mỗi khi có bản cập nhật quan trọng, để 1 lệnh --version là
# biết ngay đang chạy đúng bản mới hay vẫn là bản cũ trên máy — không
# cần nhớ lệnh grep hay so sánh --help dài dòng nữa.
SCRIPT_VERSION = "2026-09-13-append-retry-errors"

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://jobs.xomdata.com"
SOURCE_ID = "xjob"

# Không giới hạn số lượng job/category nữa — quota=None nghĩa là crawl
# TOÀN BỘ job trong khoảng --from-date/--to-date cho category đó.
CATEGORY_CONFIG: list[tuple[str, str, str, int | None]] = [
    ("BA", "business-analyst", "Business Analyst", None),
    ("DA", "data-analyst", "Data Analyst", None),
    ("DE", "data-engineer", "Data Engineer", None),
    ("AI", "ai-engineer", "AI Engineer", None),
    ("DS", "data-scientist", "Data Scientist", None),
]

DEFAULT_DATE_FROM = date(2026, 7, 1)
DEFAULT_DATE_TO = date(2026, 9, 30)

JOB_LINK_RE = re.compile(r"^/jobs/(\d+)/?$")

KNOWN_LOCATIONS = [
    "TP.Hồ Chí Minh", "TP. Hồ Chí Minh", "Hồ Chí Minh", "Ho Chi Minh",
    "Hà Nội", "Ha Noi", "Đà Nẵng", "Da Nang", "Cần Thơ", "Can Tho",
    "Việt Nam", "Remote", "Hybrid",
]

KNOWN_LEVELS = [
    "Intern", "Fresher", "Junior", "Middle/Senior", "Middle", "Senior/Expert",
    "Senior", "Expert", "Leader", "Manager",
]


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
        }
    )
    return session


# ============================================================
# TEXT CLEANING
# ============================================================

def clean_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    if cleaned.startswith("="):
        cleaned = cleaned.lstrip("=")
    return cleaned.strip()


# ============================================================
# RELATIVE TIME (VI) -> DATE
# ============================================================

_RELATIVE_VN_RE = re.compile(
    r"(\d+)\s*(phút|giờ|ngày|tuần|tháng)\s*trước", re.IGNORECASE
)


def parse_relative_vn(text: str, reference: date | None = None) -> date | None:
    if not text:
        return None

    reference = reference or date.today()
    t = text.strip().casefold()

    if "hôm nay" in t or "vừa" in t or "mới đăng" in t:
        return reference

    if "hôm qua" in t:
        return reference - timedelta(days=1)

    match = _RELATIVE_VN_RE.search(t)
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)

    if unit in ("phút", "giờ"):
        delta_days = 0
    elif unit == "ngày":
        delta_days = amount
    elif unit == "tuần":
        delta_days = amount * 7
    else:
        delta_days = amount * 30

    return reference - timedelta(days=delta_days)


def is_within_date_range(d: date | None, date_from: date | None, date_to: date | None) -> bool:
    if date_from is None and date_to is None:
        return True
    if d is None:
        return True
    if date_from and d < date_from:
        return False
    if date_to and d > date_to:
        return False
    return True


# ============================================================
# __NEXT_DATA__ (best-effort)
# ============================================================

def extract_next_data(soup: BeautifulSoup) -> Any | None:
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        return None
    try:
        return json.loads(tag.string)
    except (json.JSONDecodeError, TypeError):
        return None


def _looks_like_job_object(d: dict) -> bool:
    keys_cf = {str(k).casefold() for k in d.keys()}
    has_title = any(k in keys_cf for k in ("title", "jobtitle", "name"))
    has_body = any(
        k in keys_cf
        for k in ("description", "descriptionhtml", "content", "jd", "requirements")
    )
    return has_title and has_body


def find_job_like_object(node: Any, _depth: int = 0) -> dict | None:
    if _depth > 12:
        return None

    if isinstance(node, dict):
        if _looks_like_job_object(node):
            return node
        for value in node.values():
            found = find_job_like_object(value, _depth + 1)
            if found is not None:
                return found

    elif isinstance(node, list):
        for item in node:
            found = find_job_like_object(item, _depth + 1)
            if found is not None:
                return found

    return None


def get_first(d: dict, *keys: str) -> Any:
    keys_cf = {k.casefold(): k for k in d.keys()}
    for candidate in keys:
        real_key = keys_cf.get(candidate.casefold())
        if real_key is not None and d.get(real_key) not in (None, ""):
            return d[real_key]
    return None


def _html_to_text(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    return clean_text(BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True))


# ============================================================
# SKILL / EXPERIENCE PATTERNS
# ============================================================

RAW_SKILL_PATTERNS = [
    r"\bExcel\b", r"\bGoogle Sheets\b", r"\bPower Query\b", r"\bVBA\b",
    r"\bSQL\b", r"\bMySQL\b", r"\bPostgreSQL\b", r"\bSQL Server\b", r"\bOracle\b",
    r"\bBigQuery\b", r"\bSnowflake\b", r"\bRedshift\b", r"\bClickHouse\b",
    r"\bPower BI\b", r"\bTableau\b", r"\bLooker( Studio)?\b", r"\bQlik\b", r"\bDOMO\b",
    r"\bPython\b", r"\bPandas\b", r"\bNumPy\b", r"\bR\b", r"\bSAS\b", r"\bSPSS\b",
    r"\bAlteryx\b", r"\bDbt\b", r"\bAirflow\b", r"\bSpark\b", r"\bHadoop\b", r"\bKafka\b",
    r"\bETL\b", r"\bELT\b", r"\bData Warehouse\b", r"\bData Lake\b",
    r"\bAWS\b", r"\bAzure\b", r"\bGCP\b", r"\bGoogle Cloud\b",
    r"\bDocker\b", r"\bKubernetes\b", r"\bGit\b", r"\bJira\b", r"\bConfluence\b",
    r"\bTensorFlow\b", r"\bPyTorch\b", r"\bScikit-learn\b", r"\bMachine Learning\b",
    r"\bDeep Learning\b", r"\bLLM\b", r"\bNLP\b", r"\bComputer Vision\b",
    r"\bMLOps\b", r"\bMLflow\b", r"\bKubeflow\b", r"\bVertex AI\b", r"\bSageMaker\b",
    r"\bGoogle Analytics\b", r"\bGA4\b", r"\bAppsFlyer\b", r"\bFirebase\b",
    r"\bJavaScript\b", r"\bJava\b", r"\bScala\b", r"\bGolang\b",
    r"\bAgile\b", r"\bScrum\b", r"\bJBPM\b", r"\bBPMN\b", r"\bUML\b",
]

_EXPERIENCE_RE = re.compile(
    r"(\d+\s*\+?\s*(?:-\s*\d+\s*)?"
    r"(?:năm|years|year)\b"
    r"(?:\s*(?:of\s*)?experience|\s*kinh\s*nghiệm)?)",
    re.IGNORECASE,
)


def scan_skills(text: str) -> list[str]:
    found: list[str] = []
    if not text:
        return found
    seen_cf = set()
    for pattern in RAW_SKILL_PATTERNS:
        for m in re.finditer(pattern, text, re.IGNORECASE):
            exact = m.group(0)
            if exact.casefold() not in seen_cf:
                found.append(exact)
                seen_cf.add(exact.casefold())
    return found


def scan_experience(*texts: str) -> str:
    for text in texts:
        if not text:
            continue
        match = _EXPERIENCE_RE.search(text)
        if match:
            return clean_text(match.group(1))
    return ""


# ============================================================
# DOM-BASED EXTRACTION
# ============================================================

DESCRIPTION_HEADINGS = (
    "role summary", "job description", "job summary", "overview",
    "about the role", "key responsibilities", "responsibilities",
    "main responsibilities", "mô tả công việc", "nhiệm vụ", "công việc chính",
)

REQUIREMENT_HEADINGS = (
    "required qualifications", "preferred qualifications", "requirements",
    "qualifications", "your skills and experience", "skills and experience",
    "skills & experience", "must have", "nice to have", "job requirements",
    "yêu cầu công việc", "yêu cầu ứng viên", "yêu cầu", "kỹ năng",
)

IGNORE_HEADINGS = (
    "what we offer", "benefits", "why you'll love working here",
    "quyền lợi", "phúc lợi", "chế độ",
)

_ALL_KNOWN_HEADINGS = DESCRIPTION_HEADINGS + REQUIREMENT_HEADINGS + IGNORE_HEADINGS


def _split_jd_into_sections(container: Tag) -> dict[str, str]:
    sections: dict[str, str] = {}
    bold_tags = container.find_all(["strong", "b"])

    if not bold_tags:
        return sections

    heading_tags = []
    for tag in bold_tags:
        text_cf = clean_text(tag.get_text(" ", strip=True)).casefold().rstrip(":")
        if any(h in text_cf for h in _ALL_KNOWN_HEADINGS):
            heading_tags.append((tag, text_cf))

    if not heading_tags:
        return sections

    all_text_nodes = list(container.descendants)

    def _text_between(start_tag: Tag, end_tag: Tag | None) -> str:
        collecting = False
        pieces = []
        for node in all_text_nodes:
            if node is start_tag:
                collecting = True
                continue
            if end_tag is not None and node is end_tag:
                break
            if collecting and isinstance(node, str):
                pieces.append(node)
        return clean_text(" ".join(pieces))

    for idx, (tag, heading_cf) in enumerate(heading_tags):
        next_tag = heading_tags[idx + 1][0] if idx + 1 < len(heading_tags) else None
        content = _text_between(tag, next_tag)
        heading_original = clean_text(tag.get_text(" ", strip=True))
        if heading_original and content.casefold().startswith(heading_original.casefold()):
            content = clean_text(content[len(heading_original):])
        if content:
            sections[heading_cf] = content

    return sections


def extract_from_dom(soup: BeautifulSoup, job_url: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "job_title": "", "company_name": "", "level": "", "location": "",
        "salary_hint": "", "posted_relative": "", "description_raw": "",
        "requirements_raw": "", "deadline": "",
    }

    h1 = soup.find("h1")
    if h1:
        result["job_title"] = clean_text(h1.get_text(" ", strip=True))

    company_link = soup.select_one('a[href^="/companies/"]')
    if company_link:
        raw = clean_text(company_link.get_text(" ", strip=True))
        result["company_name"] = raw.split("·")[0].strip()

    meta_zone = ""
    if company_link:
        node = company_link
        hop = 0
        texts = []
        while node is not None and hop < 6:
            node = node.find_next(string=True)
            if node is None:
                break
            texts.append(str(node))
            hop += 1
        meta_zone = " ".join(texts)
    if not meta_zone:
        meta_zone = soup.get_text(" ", strip=True)[:500]

    for loc in KNOWN_LOCATIONS:
        if loc.casefold() in meta_zone.casefold():
            result["location"] = loc
            break

    for level in KNOWN_LEVELS:
        if re.search(rf"\b{re.escape(level)}\b", meta_zone, re.IGNORECASE):
            result["level"] = level
            break

    result["posted_relative"] = meta_zone
    for t in soup(["script", "style", "noscript", "footer"]):
     t.decompose()
    full_text_container = soup.body or soup
    sections = _split_jd_into_sections(full_text_container)

    desc_parts = [v for k, v in sections.items() if any(h in k for h in DESCRIPTION_HEADINGS)]
    req_parts = [v for k, v in sections.items() if any(h in k for h in REQUIREMENT_HEADINGS)]

    if desc_parts:
        result["description_raw"] = clean_text(" ".join(desc_parts))
    if req_parts:
        result["requirements_raw"] = clean_text(" ".join(req_parts))

    if not result["description_raw"] and not result["requirements_raw"]:
        full_text = full_text_container.get_text("\n", strip=True)
        start_marker = re.search(r"(Ứng tuyển|Lưu tin|Apply)", full_text, re.IGNORECASE)
        end_marker = full_text.find("Cơ hội việc làm Data & AI")
        start_idx = start_marker.end() if start_marker else 0
        end_idx = end_marker if end_marker != -1 else len(full_text)
        if end_idx > start_idx:
            result["description_raw"] = clean_text(full_text[start_idx:end_idx])

    return result


# ============================================================
# CRAWL 1 JOB
# ============================================================

def crawl_job(job_id: str, session: requests.Session) -> dict[str, Any]:
    job_url = f"{BASE_URL}/jobs/{job_id}"
    response = session.get(job_url, timeout=30)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    from_json: dict[str, Any] = {}
    next_data = extract_next_data(soup)
    job_obj = find_job_like_object(next_data) if next_data else None

    if job_obj:
        from_json["job_title"] = clean_text(str(get_first(job_obj, "title", "jobTitle", "name") or ""))
        from_json["company_name"] = clean_text(
            str(get_first(job_obj, "company", "companyName", "employerName", "orgName") or "")
        )
        from_json["level"] = clean_text(str(get_first(job_obj, "level", "jobLevel") or ""))
        from_json["location"] = clean_text(str(get_first(job_obj, "location", "city", "address") or ""))
        from_json["description_raw"] = _html_to_text(
            get_first(job_obj, "description", "descriptionHtml", "content", "jd")
        )
        from_json["requirements_raw"] = _html_to_text(
            get_first(job_obj, "requirements", "requirement", "qualifications")
        )
        from_json["deadline"] = clean_text(str(get_first(job_obj, "deadline", "expiredAt", "expiryDate") or ""))
        posted_iso = get_first(job_obj, "postedAt", "datePosted", "publishedAt", "createdAt")
        from_json["posted_iso"] = str(posted_iso) if posted_iso else ""

    from_dom = extract_from_dom(soup, job_url)

    def pick(*values):
        for v in values:
            if v:
                return v
        return ""

    job_title = pick(from_json.get("job_title"), from_dom.get("job_title"))
    company_name = pick(from_json.get("company_name"), from_dom.get("company_name"))
    level = pick(from_json.get("level"), from_dom.get("level"))
    location = pick(from_json.get("location"), from_dom.get("location"))
    description_raw = pick(from_json.get("description_raw"), from_dom.get("description_raw"))
    requirements_raw = pick(from_json.get("requirements_raw"), from_dom.get("requirements_raw"))
    deadline = pick(from_json.get("deadline"), from_dom.get("deadline"))

    updated_at_date: date | None = None
    posted_iso = from_json.get("posted_iso", "")
    if posted_iso:
        try:
            iso_text = posted_iso[:-1] + "+00:00" if posted_iso.endswith("Z") else posted_iso
            updated_at_date = datetime.fromisoformat(iso_text).date()
        except ValueError:
            updated_at_date = None
    if updated_at_date is None:
        updated_at_date = parse_relative_vn(from_dom.get("posted_relative", ""))

    skills_raw = scan_skills(f"{requirements_raw} {description_raw}")
    experience_raw = scan_experience(requirements_raw, description_raw)

    return {
        "job_id": f"{SOURCE_ID}_{job_id}",
        "source_id": SOURCE_ID,
        "job_title": job_title,
        "company_name": company_name,
        "level": level,
        "experience_raw": experience_raw,
        "updated_at": updated_at_date.isoformat() if updated_at_date else "",
        "deadline": deadline,
        "location": location,
        "job_url": job_url,
        "description_raw": description_raw,
        "requirements_raw": requirements_raw,
        "skills_raw": skills_raw,
        "_updated_at_date": updated_at_date,
    }


# ============================================================
# COLLECT JOB IDS THEO CATEGORY
# ============================================================

def collect_job_ids(
    slug: str,
    session: requests.Session,
    *,
    limit: int | None,
    delay: float,
) -> list[str]:
    ids: list[str] = []
    page = 1

    while True:
        if limit is not None and len(ids) >= limit:
            break

        url = f"{BASE_URL}/?category={slug}" if page == 1 else f"{BASE_URL}/?category={slug}&page={page}"
        print(f"[INFO] Crawling page {page}: {url}", file=sys.stderr)

        response = session.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        found = []
        for a in soup.select('a[href^="/jobs/"]'):
            href = a.get("href", "")
            m = JOB_LINK_RE.match(urlparse(href).path)
            if m and m.group(1) not in ids and m.group(1) not in found:
                found.append(m.group(1))

        if not found:
            print("[WARN] Không tìm thấy job nào trên trang này, dừng.", file=sys.stderr)
            break

        ids.extend(found)
        print(f"[INFO] Page {page}: {len(found)} job | Tổng: {len(ids)}", file=sys.stderr)

        page += 1
        time.sleep(delay)

    if limit is not None:
        ids = ids[:limit]

    return ids


# ============================================================
# SAVE CSV
# ============================================================

CSV_FIELDNAMES = [
    "job_id", "source_id", "job_title", "company_name", "level",
    "experience_raw", "updated_at", "deadline", "location", "job_url",
    "description_raw", "requirements_raw", "skills_raw",
    "expertise_category", "error", "date_crawl", "crawl_at",
]


def load_existing_results(output: Path) -> list[dict[str, Any]]:
    """Đọc các bản ghi đã crawl TRƯỚC ĐÓ từ file CSV output (nếu đã tồn
    tại). Dùng cho chế độ --append: cho phép chạy script vào những ngày
    tiếp theo mà KHÔNG mất dữ liệu cũ, và không crawl/ghi trùng lại các
    job ĐÃ CRAWL THÀNH CÔNG sẵn có trong file (dedup theo job_url; xem
    build_seen_job_urls() để biết vì sao job từng lỗi KHÔNG được coi là
    "đã có"). Trả về [] nếu file chưa tồn tại. Tự bỏ qua dòng 'sep=,' ở
    đầu file nếu output từng được ghi với --excel-sep-hint."""
    if not output.exists():
        return []

    with output.open("r", newline="", encoding="utf-8-sig") as file:
        first_line = file.readline()
        if not first_line.strip().lower().startswith("sep="):
            file.seek(0)
        reader = csv.DictReader(file)
        return list(reader)


def build_seen_job_urls(existing_results: list[dict[str, Any]]) -> set[str]:
    """Trả về tập job_url được coi là 'đã crawl xong, không cần thử lại'.

    QUAN TRỌNG (fix bug --append cũ): CHỈ tính các bản ghi crawl THÀNH
    CÔNG (error rỗng/không có). Bản ghi cũ có error (timeout, 404, parse
    lỗi...) KHÔNG được đưa vào đây, để lần --append tiếp theo sẽ tự
    động crawl lại các job từng lỗi thay vì bỏ qua vĩnh viễn.
    """
    return {
        r.get("job_url", "")
        for r in existing_results
        if r.get("job_url") and not r.get("error")
    }


def save_results(data: list[dict[str, Any]], output: Path, *, excel_sep_hint: bool = False):
    """
    excel_sep_hint=True: thêm dòng `sep=,` ở ĐẦU file — đây là cú pháp
    Excel (Windows) tự nhận diện để LUÔN tách cột theo dấu phẩy, bất kể
    locale máy đang set dấu phẩy là ký tự thập phân (mặc định ở VN) hay
    không. Đây là nguyên nhân phổ biến nhất khiến double-click mở CSV
    trong Excel bị dồn hết dữ liệu vào 1 cột (trong khi VSCode/pandas
    đọc CSV thô nên không bị ảnh hưởng).
    LƯU Ý: dòng `sep=,` là quy ước RIÊNG của Excel — nếu bạn định đọc
    lại file này bằng pandas/code khác, cần bỏ qua dòng đầu tiên
    (pd.read_csv(path, skiprows=1)) hoặc để mặc định excel_sep_hint=False.
    """
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("w", newline="", encoding="utf-8-sig") as f:
        if excel_sep_hint:
            f.write("sep=,\n")
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction="ignore")
        writer.writeheader()
        for record in data:
            row = record.copy()
            if isinstance(row.get("skills_raw"), list):
                row["skills_raw"] = " | ".join(row["skills_raw"])
            writer.writerow(row)


# ============================================================
# CRAWL THEO TỪNG CATEGORY
# ============================================================

def crawl_all_categories(
    output: Path,
    *,
    category_config: list[tuple[str, str, str, int | None]],
    date_from: date | None,
    date_to: date | None,
    delay: float,
    excel_sep_hint: bool = False,
    append: bool = False,
) -> list[dict[str, Any]]:
    session = make_session()
    today_str = date.today().isoformat()

    # Chế độ --append: nạp lại bản ghi đã crawl ở lần chạy TRƯỚC.
    # - existing_results: TOÀN BỘ bản ghi cũ (kể cả job lỗi) — được giữ
    #   lại trong `results` để không mất dữ liệu, và các job lỗi cũ sẽ
    #   bị GHI ĐÈ nếu crawl lại thành công (xem vòng lặp bên dưới).
    # - seen_job_urls: CHỈ job đã crawl THÀNH CÔNG — dùng để bỏ qua khi
    #   thu thập job_ids mới (fix bug: job lỗi không bị khóa vĩnh viễn).
    existing_results: list[dict[str, Any]] = load_existing_results(output) if append else []
    results: list[dict[str, Any]] = list(existing_results)
    # Map job_url -> index trong `results`, để khi crawl lại 1 job từng
    # lỗi, ta THAY THẾ đúng vị trí cũ thay vì thêm dòng trùng job_url.
    result_index_by_url: dict[str, int] = {
        r.get("job_url", ""): i for i, r in enumerate(results) if r.get("job_url")
    }
    seen_job_urls: set[str] = build_seen_job_urls(existing_results)

    if append and existing_results:
        n_error = sum(1 for r in existing_results if r.get("error"))
        print(
            f"[INFO] --append: đã nạp {len(existing_results)} bản ghi cũ từ "
            f"{output.name} ({len(seen_job_urls)} job thành công sẽ được bỏ "
            f"qua, {n_error} job lỗi sẽ được crawl lại).",
            file=sys.stderr,
        )

    for short_key, slug, label, quota in category_config:

        print(f"\n{'=' * 60}\n{short_key} ({label}) — slug={slug} — quota={quota or 'ALL'}\n{'=' * 60}", file=sys.stderr)

        # Lấy dư 1 chút so với quota để bù các job bị lọc rớt do ngoài
        # khoảng ngày HOẶC do đã crawl thành công trước đó (--append),
        # tối đa 4x quota (hoặc không giới hạn nếu quota=None).
        collect_limit = None if quota is None else quota * 4
        raw_job_ids = collect_job_ids(slug, session, limit=collect_limit, delay=delay)

        # Loại bỏ job ĐÃ CRAWL THÀNH CÔNG (từ lần chạy trước khi
        # --append, hoặc từ category khác đã xử lý trong CÙNG lần chạy
        # này) — dựa theo job_url — TRƯỚC khi áp quota. Job từng lỗi
        # KHÔNG bị loại ở đây nên sẽ được thử crawl lại.
        job_ids = [
            jid for jid in raw_job_ids
            if f"{BASE_URL}/jobs/{jid}" not in seen_job_urls
        ]
        skipped_dup = len(raw_job_ids) - len(job_ids)

        print(
            f"[INFO] {short_key}: thu được {len(raw_job_ids)} job ID | "
            f"bỏ qua {skipped_dup} job đã crawl thành công | còn {len(job_ids)} "
            f"job cần thử crawl (mới + từng lỗi).",
            file=sys.stderr,
        )

        kept_for_category = 0

        for index, job_id in enumerate(job_ids, start=1):

            if quota is not None and kept_for_category >= quota:
                print(f"[INFO] {short_key} đã đủ quota={quota}, chuyển category tiếp theo.", file=sys.stderr)
                break

            print(f"[{short_key} {index}/{len(job_ids)}] job_id={job_id}", file=sys.stderr)

            try:
                record = crawl_job(job_id, session)
                record["error"] = ""
                print(f"  ✓ {record['job_title']} (updated_at={record['updated_at']})", file=sys.stderr)
            except Exception as error:
                print(f"  ✗ ERROR: {error}", file=sys.stderr)
                record = {
                    "job_id": f"{SOURCE_ID}_{job_id}", "source_id": SOURCE_ID,
                    "job_title": "", "company_name": "", "level": "",
                    "experience_raw": "", "updated_at": "", "deadline": "",
                    "location": "", "job_url": f"{BASE_URL}/jobs/{job_id}",
                    "description_raw": "", "requirements_raw": "", "skills_raw": [],
                    "error": str(error), "_updated_at_date": None,
                }

            record["expertise_category"] = label
            record["date_crawl"] = today_str
            record["crawl_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            if not record.get("error") and not is_within_date_range(
                record.get("_updated_at_date"), date_from, date_to
            ):
                print(f"  (bỏ qua, ngoài khoảng ngày lọc: {record.get('updated_at')!r})", file=sys.stderr)
            else:
                record.pop("_updated_at_date", None)

                existing_pos = result_index_by_url.get(record["job_url"])
                if existing_pos is not None:
                    # Job này từng có mặt (nhiều khả năng là job lỗi cũ
                    # đang được thử lại) -> GHI ĐÈ đúng vị trí cũ thay
                    # vì thêm dòng mới trùng job_url.
                    results[existing_pos] = record
                else:
                    results.append(record)
                    result_index_by_url[record["job_url"]] = len(results) - 1

                if not record.get("error"):
                    seen_job_urls.add(record["job_url"])
                    kept_for_category += 1

                save_results(results, output, excel_sep_hint=excel_sep_hint)

            if index < len(job_ids):
                time.sleep(delay)

        print(f"[INFO] {short_key}: giữ lại {kept_for_category} job mới (quota={quota or 'ALL'}).", file=sys.stderr)

    return results


# ============================================================
# MAIN
# ============================================================

def parse_date_arg(value: str) -> date:
    try:
        return datetime.strptime(value, "%d/%m/%Y").date()
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"Ngày không hợp lệ '{value}', dùng DD/MM/YYYY") from exc


def main():
    parser = argparse.ArgumentParser(
        description="Crawl Xóm Jobs (jobs.xomdata.com) theo domain BA/DA/DE/AI/DS, lọc theo ngày đăng."
    )
    parser.add_argument(
        "--version", action="version", version=f"crawl_xomjobs.py {SCRIPT_VERSION}",
        help="In phiên bản script rồi thoát — dùng để kiểm tra đang chạy đúng bản mới hay bản cũ.",
    )
    parser.add_argument("-o", "--output", type=Path, default=Path("xomjobs.csv"), help="Output CSV")
    parser.add_argument("--from-date", type=parse_date_arg, default=DEFAULT_DATE_FROM,
                         help="Ngày đăng >= (DD/MM/YYYY), mặc định 01/07/2026")
    parser.add_argument("--to-date", type=parse_date_arg, default=DEFAULT_DATE_TO,
                         help="Ngày đăng <= (DD/MM/YYYY), mặc định 30/09/2026")
    parser.add_argument("--no-date-filter", action="store_true", help="Tắt lọc theo ngày")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay giữa các request")
    parser.add_argument(
        "--limit-per-category", type=int, default=None,
        help=(
            "GHI ĐÈ quota mặc định (mặc định hiện tại: KHÔNG giới hạn cho "
            "mọi category) bằng CÙNG 1 giá trị cho MỌI category — dùng để "
            "test nhanh, vd --limit-per-category 3"
        ),
    )
    parser.add_argument(
        "--excel-sep-hint", action="store_true",
        help=(
            "Thêm dòng 'sep=,' đầu file CSV để Excel LUÔN tách cột theo "
            "dấu phẩy (fix lỗi Excel dồn hết dữ liệu vào 1 cột do locale "
            "VN mặc định dùng dấu phẩy làm ký tự thập phân). Nếu định đọc "
            "lại file bằng pandas, nhớ dùng skiprows=1."
        ),
    )
    parser.add_argument(
        "--append", action="store_true",
        help=(
            "Nối kết quả vào file CSV đã có ở --output (nếu tồn tại) thay "
            "vì ghi đè. Tự động bỏ qua (không crawl lại) các job ĐÃ CRAWL "
            "THÀNH CÔNG sẵn có trong file (dựa theo job_url); các job "
            "từng bị lỗi ở lần chạy trước SẼ được thử crawl lại. Dùng để "
            "crawl tiếp vào những ngày sau mà không bị trùng lặp bản ghi."
        ),
    )
    args = parser.parse_args()

    if args.from_date > args.to_date:
        parser.error("--from-date phải <= --to-date")

    date_from = None if args.no_date_filter else args.from_date
    date_to = None if args.no_date_filter else args.to_date

    category_config = CATEGORY_CONFIG
    if args.limit_per_category is not None:
        category_config = [
            (k, slug, label, args.limit_per_category)
            for (k, slug, label, _quota) in CATEGORY_CONFIG
        ]

    results = crawl_all_categories(
        args.output,
        category_config=category_config,
        date_from=date_from,
        date_to=date_to,
        delay=args.delay,
        excel_sep_hint=args.excel_sep_hint,
        append=args.append,
    )

    errors = sum(bool(r.get("error")) for r in results)
    print(f"\nHoàn tất: {len(results)} job, {errors} lỗi.")
    print(f"File lưu tại: {args.output.resolve()}")


if __name__ == "__main__":
    main()