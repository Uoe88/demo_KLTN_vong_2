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
- skills_raw (Giữ nguyên văn bản thô, không chuẩn hóa)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
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


def extract_salary(soup: BeautifulSoup, job_schema: dict[str, Any]) -> str:
    for use_tag in soup.select('use[href$="#currency-dollar"], use[href$="#dollar"]'):
        container = use_tag.find_parent(["div", "span"])
        if container:
            val = clean_text(container.get_text(" ", strip=True))
            if val:
                return val

    salary_tag = soup.select_one(".salary, .job-salary, .text-salary")
    if salary_tag:
        val = clean_text(salary_tag.get_text(" ", strip=True))
        if val:
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
            return f"{value} {currency}".strip()

    return ""


def extract_experience(soup: BeautifulSoup, requirements_raw: str) -> str:
    # 1. Lấy từ badge UI nếu có
    exp_list = values_after_label(
        soup,
        ("Kinh nghiệm", "Experience", "Min experience", "Kinh nghiệm tối thiểu"),
    )
    if exp_list:
        return " | ".join(exp_list)

    # 2. Quét trong text: Hỗ trợ cấu trúc "5+ years of experience", "3-5 years", "2 năm kinh nghiệm"
    if requirements_raw:
        pattern = r"(\d+\s*\+?\s*(?:-\s*\d+\s*)?(?:năm|year|years)(?:\s*(?:of\s*)?experience|\s*kinh\s*nghiệm)?)"
        match = re.search(pattern, requirements_raw, re.IGNORECASE)
        if match:
            return clean_text(match.group(1))

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

def extract_skills_raw(
    soup: BeautifulSoup,
    schema: dict[str, Any],
    requirements_raw: str,
) -> list[str]:
    """
    Trích xuất từ khóa kỹ năng dạng RAW DATA (nguyên bản từ JD).
    Không gộp/không đổi tên để phục vụ bước Data Cleaning & Taxonomy (Phase 2).
    """
    # 1. Trích xuất từ Badge/Tag hiển thị trực tiếp trên giao diện
    skills = values_after_label(soup, ("Kỹ năng", "Skills", "Technical skills"))
    if skills:
        return skills

    # 2. Lấy nguyên bản từ JSON-LD Schema
    schema_skills = schema.get("skills")
    if schema_skills:
        if isinstance(schema_skills, list):
            return [clean_text(str(s)) for s in schema_skills if str(s).strip()]
        if isinstance(schema_skills, str):
            return [clean_text(s) for s in re.split(r"[,;|]", schema_skills) if s.strip()]

    # 3. Quét trong văn bản Yêu cầu bằng danh mục RAW_PATTERNS mở rộng
    if requirements_raw:
        RAW_PATTERNS = [
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
            r"\bPostgreSQL\b", r"\bPostgres\b", r"\bSQL Server\b", r"\bMariaDB\b",
            r"\bMySQL\b", r"\bOracle\b", r"\bSQLite\b", r"\bSQL\b",
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
            r"\bAirflow\b", r"\bHadoop\b", r"\bSpark\b", r"\bFlink\b", r"\bDbt\b", r"\bETL\b"
        ]

        found_raw = []
        for pattern in RAW_PATTERNS:
            for match in re.finditer(pattern, requirements_raw, re.IGNORECASE):
                exact_word = match.group(0)  # Giữ nguyên bản cách viết hoa/thường trong JD
                if exact_word not in found_raw:
                    found_raw.append(exact_word)

        if found_raw:
            return found_raw

    return []


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
    
    # Trích xuất RAW skills (không qua bộ lọc chuẩn hóa)
    skills_raw = extract_skills_raw(soup, schema, requirements_raw)
    
    level = extract_level(soup, schema)
    salary_raw = extract_salary(soup, schema)
    experience_raw = extract_experience(soup, requirements_raw)
    updated_at = clean_text(str(schema.get("datePosted", "")))
    deadline = clean_text(str(schema.get("validThrough", "")))
    location = find_location(soup, schema)

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


def save_results(data: list[dict[str, Any]], output: Path):
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "job_id", "source_id", "job_title", "company_name", "level",
        "salary_raw", "experience_raw", "updated_at", "deadline",
        "location", "job_url", "description_raw", "requirements_raw",
        "skills_raw", "error",
    ]

    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for record in data:
            row = record.copy()
            if isinstance(row.get("skills_raw"), list):
                row["skills_raw"] = " | ".join(row["skills_raw"])
            writer.writerow(row)


def crawl_all_jobs(
    search_url: str,
    output: Path,
    *,
    max_pages: int | None = None,
    limit: int | None = None,
    delay: float = 2.0,
):
    session = make_session()
    job_urls = collect_job_urls(search_url, session, max_pages=max_pages, delay=delay)

    if limit is not None:
        job_urls = job_urls[:limit]

    results = []
    for index, job_url in enumerate(job_urls, start=1):
        print(f"\n[{index}/{len(job_urls)}] {job_url}", file=sys.stderr)
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
            }

        results.append(result)
        save_results(results, output)

        if index < len(job_urls):
            time.sleep(delay)

    return results


# ============================================================
# MAIN ENTRYPOINT
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Crawl Raw Job Data từ ITviec (Phase 1)")
    parser.add_argument("url", nargs="?", default=DEFAULT_URL, help="URL search hoặc URL job detail")
    parser.add_argument("-o", "--output", type=Path, default=Path("raw_jobs.csv"), help="Output CSV Path")
    parser.add_argument("--max-pages", type=int, help="Số trang tối đa")
    parser.add_argument("--limit", type=int, help="Số job tối đa (ví dụ: --limit 150 để thu 100-200 job)")
    parser.add_argument("--delay", type=float, default=2.0, help="Delay giữa các request")

    args = parser.parse_args()

    if is_job_detail_url(args.url):
        session = make_session()
        result = crawl_job(args.url, session)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        save_results([result], args.output)
    else:
        results = crawl_all_jobs(
            args.url,
            args.output,
            max_pages=args.max_pages,
            limit=args.limit,
            delay=args.delay,
        )
        errors = sum(bool(item.get("error")) for item in results)
        print(f"\nHoàn tất: {len(results)} jobs, {errors} lỗi.")
        print(f"File lưu tại: {args.output.resolve()}")


if __name__ == "__main__":
    main()