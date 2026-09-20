#!/usr/bin/env python3
"""
build_skill_taxonomy.py  -  Phase 2b: taxonomy kỹ năng + bảng ánh xạ biến thể + ma trận binary_skill

Chạy (sau clean_jobs.py):
    python build_skill_taxonomy.py --input clean_out/jobs_clean.csv --outdir clean_out --min-jobs 50
Hard skill vào binary nếu: (a) >= --min-jobs job, HOẶC (b) chiếm >= --role-min-df trong ít nhất 1 nhóm nghề mục tiêu
(--roles) và >= --role-min-jobs job (giữ skill cốt lõi của nhóm nhỏ như Data Scientist). Ngoại ngữ luôn được giữ.

Đầu ra:
    skill_taxonomy.csv    canonical | group | subgroup | type(hard/soft) | n_jobs | df_pct | theo nguồn | tier | in_binary
    skill_alias_map.csv   biến thể trong dữ liệu (raw) -> canonical (+ group, type, số lần xuất hiện)
    skill_unmapped.csv    token chưa ánh xạ (sort theo tần suất) -> bổ sung vào TAXONOMY rồi chạy lại
    job_skill_binary.csv  job_uid | has_skills | n_hard_skills, n_skill_groups (độ rộng / độ phức tạp stack)
                          | soft_skill (có yêu cầu >=1 ngoại ngữ) + soft_english/french/japanese | skill_* (hard skill)
                          Soft skill khác (communication, leadership...) chỉ nằm trong skill_taxonomy.csv
    skill_report.txt      độ phủ theo ngưỡng N, cờ lệch nguồn

Cách mở rộng: chỉ sửa TAXONOMY / MULTI / NOISE bên dưới. Khóa so khớp là dạng "compact"
(bỏ dấu, lowercase, bỏ mọi ký tự không phải chữ/số/+/#) nên 'Node.js' = 'NodeJS' = 'node js'
và 'problem-solving' = 'Problem Solving' không cần khai báo riêng.
"""
import argparse
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd


DEFAULT_ROLES = ["Backend", "Frontend", "Data Engineer", "Data Scientist", "AI Engineer"]   # cột expertise_category
LANGUAGES = ["English", "Japanese", "French"]        # gom chung thành cột soft_skill (+ giữ từng ngôn ngữ)


def key(s: str) -> str:
    s = str(s).replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9+#]", "", s)


# ---------------------------------------------------------------------------
# TAXONOMY:  (group, subgroup) -> {canonical: [aliases ngoài dạng compact-trùng]}
# ---------------------------------------------------------------------------
TAXONOMY = {
    ("Programming Language", "Language"): {
        "Python": [], "Java": [], "JavaScript": ["js", "es6", "ecmascript"], "TypeScript": ["ts"],
        "Go": ["golang"], "C#": ["csharp"], "C++": ["cpp"], "C": ["c language", "c programming"],
        "PHP": [], "Ruby": [], "Kotlin": [], "Swift": [], "Objective-C": ["objc"], "Scala": [], "Rust": [],
        "Dart": [], "R": ["r language", "r programming"], "Bash/Shell": ["bash", "shell", "shell scripting", "bash shell", "sh"],
        "PowerShell": [], "VBA": [],
    },
    ("Frontend", "Framework/Markup"): {
        "React": ["reactjs"], "Angular": ["angularjs"], "Vue": ["vuejs", "vue 3"], "Next.js": ["next"],
        "Nuxt.js": ["nuxt"], "HTML": ["html5"], "CSS": ["css3"], "Sass/SCSS": ["sass", "scss"],
        "Tailwind CSS": ["tailwind"], "Bootstrap": [], "Redux": ["redux toolkit"], "Webpack": [], "Vite": [], "jQuery": [],
    },
    ("Backend Framework", "Framework"): {
        "Spring": ["spring framework", "spring mvc", "spring cloud", "spring data", "spring security"],
        "Spring Boot": ["springboot"], "Hibernate/JPA": ["hibernate", "jpa"], "MyBatis": [],
        ".NET": [".net core", "dotnet", ".net framework", "asp.net", "asp.net core", "asp.net mvc"],
        "Node.js": ["node"], "NestJS": [], "Express": ["express.js"], "Django": [], "Flask": [], "FastAPI": [],
        "Laravel": [], "Ruby on Rails": ["rails", "ror"],
    },
    ("Mobile", "Platform/Framework"): {
        "Android": [], "iOS": [], "Flutter": [], "React Native": [], "Jetpack Compose": [], "SwiftUI": [], "Xcode": [],
    },
    ("Database & Storage", "Database"): {
        "SQL": ["t-sql", "sql query"], "MySQL": [], "PostgreSQL": ["postgres", "postgre"],
        "Oracle": ["oracle db", "oracle database", "pl/sql"],
        "SQL Server": ["ms sql", "mssql", "microsoft sql server", "microsoft azure sql database", "ms sql server"],
        "MongoDB": ["mongo"], "Redis": [], "NoSQL": [], "Elasticsearch": ["elastic search", "elastic", "opensearch"],
        "Cassandra": [], "DynamoDB": [], "SQLite": [], "MariaDB": [], "Neo4j": [], "ClickHouse": [],
    },
    ("Data Engineering", "Pipeline/Platform"): {
        "ETL/ELT": ["etl", "elt", "data pipeline", "data pipelines"], "Airflow": ["apache airflow", "cloud composer"],
        "Spark": ["apache spark", "pyspark"], "Kafka": ["apache kafka"], "Flink": ["apache flink"], "dbt": [],
        "Data Warehouse": ["data warehousing", "dwh"], "Data Lake": ["lakehouse", "data lakehouse"],
        "Data Modeling": ["data modelling"], "Big Data": [], "NiFi": ["apache nifi"], "Talend": [], "Informatica": [],
        "SSIS": [], "Snowflake": [], "BigQuery": ["google bigquery"], "Redshift": [], "Databricks": [], "Hadoop": ["apache hadoop"],
        "Hive": [], "Azure Synapse": ["synapse"], "Microsoft Fabric": ["fabric"], "Data Quality": ["data quality tools", "great expectations"],
        "RabbitMQ": [], "Message Queue": ["mq", "message broker", "messaging"], "Pub/Sub": ["pubsub", "google pub/sub"],
    },
    ("Data Analytics & BI", "Analytics/BI"): {
        "Power BI": ["powerbi", "microsoft power bi", "power query", "dax"], "Tableau": ["tableu"],
        "Looker": ["looker studio", "google data studio", "data studio"], "Metabase": [], "Superset": ["apache superset"],
        "Qlik": ["qlikview", "qlik sense"], "Excel": ["microsoft excel", "google sheets", "spreadsheet", "advanced excel"],
        "Data Analysis": ["data analytics", "analytics"], "Business Intelligence": ["bi", "bi tools"],
        "A/B Testing": ["ab testing", "a/b test", "experimentation"], "Statistics": ["statistical analysis"],
        "Data Visualization": ["data viz", "visualization", "dashboard", "dashboards"], "Data Mining": [],
    },
    ("AI/ML", "ML/AI"): {
        "Machine Learning": ["ml"], "Deep Learning": ["dl"], "NLP": ["natural language processing"],
        "Computer Vision": ["cv", "image processing", "opencv"], "LLM": ["llms", "large language model", "large language models"],
        "Generative AI": ["genai", "gen ai"], "RAG": ["retrieval augmented generation"],
        "Agentic AI": ["ai agent", "ai agents", "agentic", "multi-agent"], "Prompt Engineering": [],
        "LangChain": ["langgraph"], "Hugging Face": ["transformers"], "PyTorch": [], "TensorFlow": ["keras"],
        "scikit-learn": ["sklearn"], "Pandas": [], "NumPy": [], "MLOps": [], "MLflow": [], "Kubeflow": [],
        "Vertex AI": [], "SageMaker": ["amazon sagemaker"], "OpenAI": ["openai api", "gpt"], "OCR": [],
        "Data Science": ["data scientist"], "Artificial Intelligence": ["ai", "artificial intelligence", "ai/ml"],
    },
    ("AI Dev Tools", "AI coding assistant"): {
        "Claude": ["claude code", "anthropic"], "GitHub Copilot": ["copilot", "microsoft copilot"], "Cursor": ["cursor ai"],
        "ChatGPT": [], "Gemini": ["google gemini"], "Windsurf": [],
    },
    ("Cloud", "Platform"): {
        "AWS": ["amazon web services", "s3", "ec2", "lambda", "aws lambda", "cloudformation", "aws cloudformation"],
        "Azure": ["microsoft azure"], "GCP": ["google cloud", "google cloud platform"], "Alibaba Cloud": ["aliyun"],
        "Firebase": [], "Supabase": [], "Serverless": [],
        "Cloud Computing": ["cloud", "cloud native", "cloud-native architecture", "cloud infrastructure", "hybrid cloud", "public cloud", "cloud migration"],
    },
    ("DevOps & Infra", "Tooling"): {
        "CI/CD": ["continuous integration", "continuous delivery", "continuous deployment"],
        "Docker": ["containerization", "containers"], "Kubernetes": ["k8s", "aks", "gke", "openshift"],
        "Terraform": [], "Infrastructure as Code": ["iac"], "Ansible": [], "Jenkins": [],
        "GitHub Actions": ["github action"], "GitLab CI": ["gitlab ci/cd", "gitlab pipelines"], "ArgoCD": ["argo cd", "argo"],
        "Helm": [], "Prometheus": [], "Grafana": [], "Datadog": [], "ELK": ["elk stack", "elastic stack", "kibana", "logstash"],
        "Splunk": [], "OpenTelemetry": ["otel"], "Observability": ["monitoring", "logging", "apm"], "CircleCI": [],
        "Linux": ["unix", "linux administration"], "Nginx": [], "Istio": ["service mesh"], "Vault": ["hashicorp vault"],
        "Pulumi": [], "VMware": ["vsphere"], "Networking": ["network", "tcp/ip"], "DevOps": ["sre", "site reliability"],
    },
    ("Security", "Security"): {
        "Cybersecurity": ["cyber security", "information security", "infosec", "security", "it security", "network security"],
        "SIEM": ["wazuh", "qradar"], "SOC": ["security operations center", "soc analyst"],
        "Penetration Testing": ["pentest", "pen test", "pentesting", "vapt", "red team", "ethical hacking"],
        "OWASP": ["owasp top 10"], "Burp Suite": ["burp"], "Metasploit": [], "Nmap": [], "Wireshark": [], "WAF": [], "DLP": [],
        "IDS/IPS": ["ids", "ips", "intrusion detection"], "EDR": ["xdr"], "SOAR": [], "Firewall": ["firewalls"],
        "Zero Trust": ["zero trust architecture"], "IAM": ["identity & access management", "identity and access management", "pam", "okta", "keycloak"],
        "OAuth": ["oauth2", "openid connect", "oidc"], "JWT": [], "SSO": [], "SAML": [],
        "Cloud Security": [], "Application Security": ["appsec"], "SAST": [], "DAST": [], "DevSecOps": [],
        "Incident Response": ["dfir"], "Vulnerability Management": ["vulnerability assessment", "vulnerability scanning"],
        "ISO 27001": ["iso/iec 27001"], "PCI DSS": [], "SOC 2": [], "NIST": ["nist csf"],
        "GRC": ["governance, risk & compliance", "governance", "risk & compliance", "compliance"],
        "Risk Management": [], "Data Privacy": ["data privacy / compliance", "gdpr", "privacy"],
    },
    ("Certification", "Cert"): {
        "CISSP": [], "ITIL": ["itil foundation"], "CEH": [], "OSCP": [], "CISM": [], "CISA": [], "CompTIA": ["security+", "comptia security+"], "TOGAF": [],
    },
    ("Testing/QA", "Testing"): {
        "Software Testing": ["qa qc", "qa", "qc", "tester", "testing", "manual testing", "manual test", "software tester", "quality assurance"],
        "Automation Testing": ["automation test", "test automation", "automated testing"],
        "Selenium": [], "Cypress": [], "Playwright": [], "Appium": [], "JMeter": ["apache jmeter"], "Postman": [],
        "JUnit": [], "Mockito": [], "Jest": [], "pytest": [], "Robot Framework": [], "k6": [],
        "Unit Testing": ["unit test", "unit tests"], "Integration Testing": ["integration test"],
        "Performance Testing": ["load testing", "performance test"],
    },
    ("Architecture & API", "Design"): {
        "Microservices": ["microservice", "microservices architecture"], "Event-Driven": ["event driven", "event-driven architecture", "eda"],
        "REST API": ["api", "apis", "restful", "restful api", "rest", "web api", "api development", "api design"],
        "GraphQL": [], "gRPC": [], "WebSocket": ["websockets"], "SOAP": [], "OpenAPI/Swagger": ["openapi", "swagger"],
        "Software Architecture": ["system architecture", "solution architecture", "technical architecture", "system design",
                                  "architecture", "solution design", "software architect"],
        "Enterprise Architecture": [], "Domain-Driven Design": ["ddd"], "CQRS": [], "Clean Architecture": [],
        "SOLID": ["solid principles"], "OOP": ["object-oriented programming", "object oriented"], "Design Patterns": ["design pattern"],
        "MVC": [], "MVVM": [], "Monolith": [], "UML": [], "Middleware": [],
        "Data Structures & Algorithms": ["algorithms", "data structures", "dsa"],
    },
    ("Methodology & Tools", "Process/Tool"): {
        "Agile": ["agile methodology"], "Scrum": [], "Kanban": [], "Waterfall": ["waterfall methodology"], "SDLC": [],
        "Jira": [], "Confluence": [], "Trello": [], "Slack": [], "Git": ["version control"], "GitHub": [], "GitLab": [],
        "Bitbucket": [], "Maven/Gradle": ["maven", "gradle"], "PowerPoint": ["microsoft powerpoint"],
    },
    ("BA/PM & Product", "BA/PM"): {
        "Business Analysis": ["business analyst", "ba", "requirement analysis", "requirements analysis", "requirement gathering",
                              "requirements gathering", "requirements elicitation"],
        "BRD": [], "SRS": [], "User Story": ["user stories"], "Use Case": ["use cases"], "BPMN": ["business process modeling"],
        "Wireframing": ["wireframe", "prototype", "prototyping", "mockup"], "UAT": [],
        "Project Management": ["project manager", "pmp"], "Product Management": ["product manager", "product strategy"],
        "Product Owner": ["po"], "Presales": ["presale"], "Consulting": ["it consulting"],
        "Diagramming Tools": ["visio", "draw.io"],
    },
    ("Business & Domain", "Platform"): {
        "ERP": [], "SAP": ["abap"], "CRM": [], "Salesforce": [], "Odoo": [], "Shopify": [], "WordPress": [],
        "HRM/HCM": ["hrm", "hcm", "hris"], "Core Banking": [], "Power Automate": ["power apps"],
    },
    ("Other Tech", "Other"): {
        "Blockchain": ["web3", "smart contract", "smart contracts", "solidity"], "Embedded": ["embedded systems", "firmware"],
        "Unity": [], "IT Support": ["helpdesk"], "System Administration": ["system admin", "sysadmin", "windows server", "active directory"],
    },
    ("Design/UI-UX", "Design"): {
        "Figma": [], "UI/UX": ["ux", "ui", "ux design", "ui design", "user experience", "product design"],
        "Design Systems": ["design system"], "Adobe Creative Suite": ["adobe", "photoshop", "adobe photoshop", "illustrator"],
    },
    # ---- SOFT SKILL (ngoại ngữ nằm ở đây) -------------------------------------------------
    ("Soft Skill", "Language"): {
        "English": ["tieng anh", "ielts", "toeic", "toefl", "english communication", "fluent english"],
        "Japanese": ["japanese it communication", "jlpt", "tieng nhat"], "Chinese": ["mandarin", "tieng trung", "hsk"],
        "French": [], "Korean": ["tieng han", "topik"], "German": [],
    },
    ("Soft Skill", "Interpersonal"): {
        "Communication": ["communication skills", "giao tiep", "interpersonal skills", "presentation skills"],
        "Teamwork": ["team work", "team player", "collaboration", "lam viec nhom"], "Adaptability": ["adaptable", "flexibility"],
    },
    ("Soft Skill", "Cognitive"): {
        "Problem Solving": ["giai quyet van de"], "Critical Thinking": ["analytical thinking", "analytical skills", "logical thinking"],
        "Time Management": ["organization", "organizational skills"], "Attention to Detail": ["detail-oriented"],
    },
    ("Soft Skill", "Management"): {
        "Leadership": ["leadership skills", "lanh dao"], "Team Management": ["people management", "team leadership", "mentoring", "coaching"],
        "Stakeholder Management": ["stakeholder", "stakeholders"],
    },
}

# 1 token -> nhiều canonical (dịch vụ gắn với nền tảng cha)
MULTI = {
    "eks": ["Kubernetes", "AWS"], "ecs": ["AWS", "Docker"], "embedded c": ["C", "Embedded"],
    "gitlab ci": ["GitLab CI", "CI/CD"], "github actions": ["GitHub Actions", "CI/CD"], "jenkins": ["Jenkins", "CI/CD"],
    "react native": ["React Native", "React"], "next.js": ["Next.js", "React"], "spring boot": ["Spring Boot", "Spring"],
    "pyspark": ["Spark", "Python"], "sagemaker": ["SageMaker", "AWS"], "redshift": ["Redshift", "AWS", "Data Warehouse"],
    "dynamodb": ["DynamoDB", "AWS", "NoSQL"], "bigquery": ["BigQuery", "GCP", "Data Warehouse"],
    "snowflake": ["Snowflake", "Data Warehouse"], "azure synapse": ["Azure Synapse", "Azure", "Data Warehouse"],
    "vertex ai": ["Vertex AI", "GCP"],
}

# token KHÔNG phải kỹ năng: tag ngành của LinkedIn / tag phân loại job
NOISE = {key(x) for x in [
    "Engineering and Information Technology", "Information Technology", "Information Technology and Engineering", "Engineering",
    "Information Technology and Consulting", "Art/Creative", "Management and Manufacturing", "Manufacturing", "Sales", "Marketing",
    "Public Relations", "Other", "Games", "Design", "Database", "Fullstack", "Data Engineer", "Mobile Apps", "Writing/Editing",
    "Governance", "Finance", "Healthcare",
]} - {key("Governance")}          # 'Governance' đã map vào GRC
FLAGS = {key("Fresher Accepted"): "accepts_fresher", key("Internship Accepted"): "accepts_intern"}
NOISE_PREFIX = ("and ",)          # 'and Information Technology', 'and Engineering'...
NOISE_RE = re.compile(r"information technology|general business|business development|education and training|finance and sales|^apache$|^data-driven$", re.I)


# Soft skill / ngoại ngữ lấy từ VĂN BẢN JD (title + requirements + description) cho mọi nguồn, KHÔNG lấy từ skills_raw:
# skills_raw của từng nguồn khác cách sinh (LinkedIn có 'communication skills', ITviec/Xóm Jobs gần như không) nên df lệch nguồn.
SOFT_TEXT = {
    "English": r"\b(english|tieng anh|ielts|toeic|toefl)\b",
    "Japanese": r"\b(japanese|tieng nhat|jlpt)\b|日本語",
    "Korean": r"\b(korean|tieng han)\b", "Chinese": r"\b(chinese|mandarin|tieng trung)\b",
    "French": r"\b(french|tieng phap)\b", "German": r"\b(german|tieng duc)\b",
    "Communication": r"\b(communication skills?|giao tiep|interpersonal|presentation skills?)\b",
    "Teamwork": r"\b(team ?work|team player|lam viec nhom|lam viec theo nhom)\b",
    "Adaptability": r"\b(adaptab\w+|fast learner|quick learner|willingness to learn|kha nang thich nghi|ham hoc hoi)\b",
    "Problem Solving": r"\b(problem[- ]?solving|giai quyet van de)\b",
    "Critical Thinking": r"\b(critical thinking|analytical (thinking|skills?)|logical thinking|tu duy (logic|phan tich|phan bien))\b",
    "Time Management": r"\b(time management|quan ly thoi gian|organi[sz]ational skills?)\b",
    "Attention to Detail": r"\b(attention to detail|detail[- ]oriented|can than)\b",
    "Leadership": r"\b(leadership|lanh dao)\b",
    "Team Management": r"\b((team|people) management|manage[sd]? (a |the |our )?(engineering |technical )?teams?|mentor(ing|s)? (junior|team|engineers|others)|coach(ing)? (junior|team)|quan ly nhom)\b",
    "Stakeholder Management": r"\b(stakeholder (management|engagement|communication)|manage stakeholders|quan ly stakeholder)\b",
}
SOFT_RX = {c: re.compile(p, re.I) for c, p in SOFT_TEXT.items()}


def soft_from_text(text):
    t = re.sub(r"\s+", " ", unicodedata.normalize("NFKD", text.replace("đ", "d").replace("Đ", "D")).encode("ascii", "ignore").decode()
               if not re.search("日本語", text) else text)
    return {c for c, rx in SOFT_RX.items() if rx.search(t)}


def build_alias():
    alias, meta = {}, {}
    for (group, sub), skills in TAXONOMY.items():
        for canon, alts in skills.items():
            meta[canon] = {"group": group, "subgroup": sub, "type": "soft" if group == "Soft Skill" else "hard", "aliases": alts}
            for a in [canon] + alts:
                k = key(a)
                if k in alias and alias[k] != [canon]:
                    raise ValueError(f"alias '{a}' trùng giữa {alias[k]} và {canon}")
                alias[k] = [canon]
    for a, canons in MULTI.items():
        assert all(c in meta for c in canons), f"MULTI có canonical lạ: {canons}"
        alias[key(a)] = canons
    return alias, meta


def slug(canon, prefix):
    s = canon.lower().replace("c++", "cpp").replace("c#", "csharp").replace(".net", "dotnet")
    return prefix + re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="clean_out/jobs_clean.csv")
    ap.add_argument("--outdir", default="clean_out")
    ap.add_argument("--min-jobs", type=int, default=50, help="ngưỡng số job tối thiểu để một skill vào ma trận binary (~1%%)")
    ap.add_argument("--roles", default=",".join(DEFAULT_ROLES), help="nhóm nghề mục tiêu (expertise_category), cách nhau bằng dấu phẩy")
    ap.add_argument("--role-min-df", type=float, default=0.10, help="ngưỡng % trong 1 nhóm nghề để giữ skill cốt lõi (0.10 = 10%%)")
    ap.add_argument("--role-min-jobs", type=int, default=10, help="số job tối thiểu trong nhóm nghề để ngưỡng trên có nghĩa")
    a = ap.parse_args()
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    out = Path(a.outdir)
    df = pd.read_csv(a.input, usecols=["job_uid", "source", "expertise_category", "job_title", "skills_raw", "requirements_raw", "description_raw"])
    alias, meta = build_alias()
    assert all(c in meta for c in SOFT_TEXT), "SOFT_TEXT có canonical lạ"

    var_cnt = Counter()                       # (raw, canonicals)
    unmapped = Counter()
    job_skills, job_flags = {}, {}
    noise_cnt = Counter()
    for uid, val, ti, rq, ds in zip(df.job_uid, df.skills_raw, df.job_title, df.requirements_raw, df.description_raw):
        sk, fl = set(), set()
        if isinstance(val, str):
            for tok in re.split(r"\s*\|\s*", val):
                tok = tok.strip()
                if not tok:
                    continue
                k = key(tok)
                if k in alias:
                    sk.update(alias[k]); var_cnt[(tok, "|".join(alias[k]))] += 1
                elif k in FLAGS:
                    fl.add(FLAGS[k])
                elif k in NOISE or tok.lower().startswith(NOISE_PREFIX) or NOISE_RE.search(tok):
                    noise_cnt[tok] += 1
                else:
                    unmapped[tok] += 1
        sk = {c for c in sk if meta[c]["type"] == "hard"}                       # soft từ skills_raw bị bỏ (lệch nguồn)
        sk |= soft_from_text(" \n ".join(x for x in (ti, rq, ds) if isinstance(x, str)))
        job_skills[uid], job_flags[uid] = sk, fl

    has = df.skills_raw.notna() & (df.skills_raw.str.strip() != "")
    n_jobs = int(has.sum())
    src = dict(zip(df.job_uid, df.source))

    # ---- thống kê theo canonical ----
    cnt, by_src, by_role = Counter(), defaultdict(Counter), defaultdict(Counter)
    role_of = dict(zip(df.job_uid, df.expertise_category))
    has_map = dict(zip(df.job_uid, has))
    for uid, sk in job_skills.items():
        for c in sk:
            cnt[c] += 1; by_src[c][src[uid]] += 1
            if meta[c]["type"] == "hard" and role_of[uid] in roles:
                by_role[c][role_of[uid]] += 1
    role_tot = df[has & df.expertise_category.isin(roles)].expertise_category.value_counts().to_dict()   # mẫu số: job có skills_raw
    src_tot_h = df[has].source.value_counts().to_dict()
    src_tot_a = df.source.value_counts().to_dict()
    rows = []
    for c, m in meta.items():
        n = cnt.get(c, 0)
        den, st = (n_jobs, src_tot_h) if m["type"] == "hard" else (len(df), src_tot_a)
        shares = {s: by_src[c].get(s, 0) / st[s] for s in st}                    # df trong từng nguồn
        rdf = {r: by_role[c].get(r, 0) / role_tot[r] for r in roles if role_tot.get(r)}
        best_role = max(rdf, key=rdf.get) if rdf else None
        best_n = by_role[c].get(best_role, 0) if best_role else 0
        if m["type"] == "hard" and n >= a.min_jobs:
            reason = "global"
        elif m["type"] == "hard" and best_role and best_n >= a.role_min_jobs and rdf[best_role] >= a.role_min_df:
            reason = "role_core"
        elif c in LANGUAGES:
            reason = "language"
        else:
            reason = ""
        rows.append({"canonical": c, "group": m["group"], "subgroup": m["subgroup"], "type": m["type"],
                     "signal": "skills_raw" if m["type"] == "hard" else "jd_text",
                     "n_jobs": n, "df_pct": round(100 * n / den, 2),
                     **{f"df_{s}_pct": round(100 * v, 1) for s, v in shares.items()},
                     "tier": "A (>=5%)" if n / den >= .05 else "B (2-5%)" if n / den >= .02 else "C (1-2%)" if n / den >= .01 else "D (<1%)",
                     "max_role_df_pct": round(100 * rdf[best_role], 1) if best_role and m["type"] == "hard" else None,
                     "max_role": best_role if m["type"] == "hard" else None,
                     "in_binary": bool(reason), "binary_reason": reason, "aliases": "; ".join(m["aliases"])})
    tax = pd.DataFrame(rows).sort_values(["type", "n_jobs"], ascending=[True, False])
    tax.to_csv(out / "skill_taxonomy.csv", index=False, encoding="utf-8-sig")

    # ---- alias map ----
    am = pd.DataFrame([{"raw_variant": r, "canonical": c, "n_occurrences": n,
                        "group": meta[c.split("|")[0]]["group"], "type": meta[c.split("|")[0]]["type"]}
                       for (r, c), n in var_cnt.items()]).sort_values(["canonical", "n_occurrences"], ascending=[True, False])
    am.to_csv(out / "skill_alias_map.csv", index=False, encoding="utf-8-sig")
    um = pd.DataFrame(unmapped.most_common(), columns=["token", "n"])
    um.to_csv(out / "skill_unmapped.csv", index=False, encoding="utf-8-sig")

    # ---- binary ----
    sel = tax[tax.in_binary]
    hard_cols = {slug(r.canonical, "skill_"): r.canonical for _, r in sel[sel.type == "hard"].iterrows()}
    lang_cols = {f"soft_{l.lower()}": l for l in LANGUAGES}
    bin_rows = []
    for uid in df.job_uid:
        sk = job_skills[uid]
        hs = {c for c in sk if meta[c]["type"] == "hard"}
        ok = has_map[uid]                         # có skills_raw -> đếm độ rộng được; không có -> NaN (không phải 0)
        row = {"job_uid": uid, "has_skills": int(ok), **{f: int(f in job_flags[uid]) for f in FLAGS.values()},
               "n_hard_skills": len(hs) if ok else None,
               "n_skill_groups": len({meta[c]["group"] for c in hs}) if ok else None,
               "soft_skill": int(any(l in sk for l in LANGUAGES))}
        row.update({col: int(l in sk) for col, l in lang_cols.items()})
        row.update({col: int(c in sk) for col, c in hard_cols.items()})
        bin_rows.append(row)
    pd.DataFrame(bin_rows).to_csv(out / "job_skill_binary.csv", index=False)

    # ---- report ----
    hard = tax[tax.type == "hard"]; soft = tax[tax.type == "soft"]
    tot_mentions = sum(cnt[c] for c in hard.canonical)
    rep = [f"Job có skills_raw: {n_jobs}/{len(df)}",
           f"Token: mapped(tổng lượt) {sum(var_cnt.values())}, noise {sum(noise_cnt.values())}, "
           f"flag {sum(1 for f in job_flags.values() for _ in f)}, CHƯA map {sum(unmapped.values())} lượt / {len(unmapped)} token khác nhau",
           "", "Độ phủ HARD skill theo số cột giữ lại (xếp theo tần suất):",
           f"{'N':>5} {'min_jobs':>9} {'%mentions':>10} {'%jobs>=5 skill':>15} {'%jobs>=8 skill':>15}"]
    order = hard.sort_values("n_jobs", ascending=False).canonical.tolist()
    for n_top in (30, 50, 80, 100, 120, 150, 200):
        keep = set(order[:n_top])
        m = sum(cnt[c] for c in keep) / tot_mentions
        per_job = pd.Series({u: len(s & keep) for u, s in job_skills.items()})
        rep.append(f"{n_top:>5} {cnt[order[min(n_top, len(order)) - 1]]:>9} {100*m:>9.1f}% "
                   f"{100*(per_job[has.values] >= 5).mean():>14.1f}% {100*(per_job[has.values] >= 8).mean():>14.1f}%")
    for t in (5, 2, 1, 0.5):
        rep.append(f"df >= {t}%  -> {int((hard.df_pct >= t).sum())} hard, {int((soft.df_pct >= t).sum())} soft")
    rep += ["", f"in_binary: {len(hard_cols)} hard ({int((sel.binary_reason=='global').sum())} theo ngưỡng chung >= {a.min_jobs} job, "
            f"{int((sel.binary_reason=='role_core').sum())} skill cốt lõi theo nhóm nghề) + {len(lang_cols)} ngoại ngữ + soft_skill (gộp)",
            "Skill thêm nhờ 'role_core': " + ", ".join(f"{r.canonical} ({r.max_role} {r.max_role_df_pct}%)" for r in sel[sel.binary_reason == "role_core"].itertuples()),
            "", "Ngoại ngữ theo nhóm nghề mục tiêu (số job / tổng job của nhóm, từ JD text):"]
    for r_ in roles:
        ids = [u for u in df.job_uid[df.expertise_category == r_]]
        rep.append(f"  {r_:<15} n={len(ids):>4}  " + "  ".join(f"{l}={sum(l in job_skills[u] for u in ids)}" for l in LANGUAGES)
                   + f"  soft_skill(gộp)={sum(any(l in job_skills[u] for l in LANGUAGES) for u in ids)}")
    rep += [
            "", "Soft skill (từ JD text) - % job theo nguồn:"]
    rep += [f"  {r.canonical:<24} all={r.df_pct:>5}%  " + "  ".join(f"{c[3:-4]}={getattr(r, c):>5}%" for c in tax.columns if c.startswith("df_") and c.endswith("_pct") and c != "df_pct")
            for r in soft.head(10).itertuples()]
    rep += ["", "(Xem thêm cột df_<nguồn>_pct trong skill_taxonomy.csv: hard skill lệch nguồn thường do cơ cấu vị trí (Xóm Jobs chủ yếu Data/AI), không phải lỗi trích xuất.)"]
    rep += ["", "Top 25 token chưa map: " + ", ".join(f"{t}({n})" for t, n in unmapped.most_common(25))]
    (out / "skill_report.txt").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


if __name__ == "__main__":
    main()
