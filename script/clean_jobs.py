#!/usr/bin/env python3
"""
clean_jobs.py  -  Phase 2: làm sạch jobs_raw (ITviec + LinkedIn + Xóm Jobs + TopCV)

Chạy:
    python clean_jobs.py --input jobs_raw.xlsx --outdir clean_out --as-of 2026-09-20

Thứ tự xử lý (thứ tự quan trọng, đừng đảo):
    0. Tạo cột `source` từ source_id, điền job_id trống (đầu vào giả định đã sửa lệch cột Xóm Jobs)
    1. Location -> city (tên thành phố/tỉnh chuẩn; địa chỉ dài thì tìm tên thành phố ở vị trí phải nhất)
    2. Khử trùng lặp theo (title, company, city) đã chuẩn hóa, gộp field thiếu; xuất dedupe_review.csv (cặp nghi trùng)
    3. Parse số năm kinh nghiệm  (experience_raw -> exp_min / exp_max, fallback từ JD text)
    4. Chuẩn hóa level  (bảng ladder 7 bậc, level range, suy luận từ title / số năm)
    5. Điền exp còn thiếu (cột riêng, có cờ nguồn)  -> xuất jobs_clean.csv + level_map.csv + cleaning_report.txt
"""
import argparse
import hashlib
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def strip_accents(s) -> str:
    s = str(s).replace("đ", "d").replace("Đ", "D")          # 'đ' không tách được bằng NFKD
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def plain(s) -> str:
    """lowercase, bỏ dấu, ký tự lạ -> khoảng trắng"""
    return re.sub(r"[^a-z0-9+#]+", " ", strip_accents(s).lower()).strip()


SOURCE_LABEL = {"ITV": "ITviec", "LNK": "LinkedIn", "topcv": "TopCV"}
SOURCE_PRIORITY = {"ITviec": 0, "LinkedIn": 1, "TopCV": 2, "XomJobs": 3}   # nguồn gốc ưu tiên hơn nguồn tổng hợp


# --------------------------------------------------------------------------
# 0. Nguồn + job_id  (cột Xóm Jobs đã được sửa tay trong file đầu vào)
# --------------------------------------------------------------------------
def add_source(df: pd.DataFrame):
    df = df.copy()
    bad = df["job_title"].astype(str).str.strip().str.lower().eq("xjob")
    if bad.any():
        raise SystemExit(f"{int(bad.sum())} dòng còn job_title='xjob': cột Xóm Jobs trong file đầu vào chưa được sửa lệch.")
    df["source"] = df["source_id"].map(SOURCE_LABEL).fillna("XomJobs")
    df["job_id"] = df["job_id"].fillna(df["source_id"])      # Xóm Jobs không có job_id -> dùng source_id
    return df


# --------------------------------------------------------------------------
# 1. Location -> city
# --------------------------------------------------------------------------
# Nhãn chuẩn (có dấu) -> thêm alias không dấu nếu tên gọi khác. Thêm/bớt tỉnh ở đây.
CITY_ALIASES = {
    "Hà Nội": ["hanoi"],
    "Hồ Chí Minh": ["hcmc", "tp hcm", "tphcm", "hcm", "sai gon", "saigon"],
    "Đà Nẵng": ["danang"], "Cần Thơ": [], "Hải Phòng": [], "Hải Dương": [], "Nghệ An": [],
    "Huế": ["hue", "thua thien hue"], "Bà Rịa - Vũng Tàu": ["ba ria", "vung tau"], "Đắk Lắk": ["dak lak", "daklak"],
}
OTHER_PROVINCES = [
    "An Giang", "Bạc Liêu", "Bắc Giang", "Bắc Kạn", "Bắc Ninh", "Bến Tre", "Bình Dương", "Bình Định", "Bình Phước",
    "Bình Thuận", "Cà Mau", "Cao Bằng", "Đắk Nông", "Điện Biên", "Đồng Nai", "Đồng Tháp", "Gia Lai", "Hà Giang", "Hà Nam",
    "Hà Tĩnh", "Hậu Giang", "Hòa Bình", "Hưng Yên", "Khánh Hòa", "Kiên Giang", "Kon Tum", "Lai Châu", "Lâm Đồng", "Lạng Sơn",
    "Lào Cai", "Long An", "Nam Định", "Ninh Bình", "Ninh Thuận", "Phú Thọ", "Phú Yên", "Quảng Bình", "Quảng Nam", "Quảng Ngãi",
    "Quảng Ninh", "Quảng Trị", "Sóc Trăng", "Sơn La", "Tây Ninh", "Thái Bình", "Thái Nguyên", "Thanh Hóa", "Tiền Giang",
    "Trà Vinh", "Tuyên Quang", "Vĩnh Long", "Vĩnh Phúc", "Yên Bái",
]
OVERSEAS = "Nước ngoài"
OVERSEAS_RE = re.compile(r"\b(overseas|france|paris|singapore|usa|united states|japan|tokyo|germany|australia|"
                         r"thailand|malaysia|philippines|korea|india|canada|uk)\b")


def _city_regexes():
    rx = {}
    for label, alts in {**CITY_ALIASES, **{p: [] for p in OTHER_PROVINCES}}.items():
        names = {plain(label)} | set(alts)
        rx[label] = re.compile(r"\b(" + "|".join(sorted((re.escape(n) for n in names), key=len, reverse=True)) + r")\b")
    return rx


CITY_RX = _city_regexes()
TITLE_CITY_TAGS = [
    ("Hà Nội", r"\b(hn|ha noi|hanoi)\b"),
    ("Hồ Chí Minh", r"\b(hcm|hcmc|tphcm|ho chi minh|sai gon|saigon)\b"),
    ("Đà Nẵng", r"\b(da nang|danang)\b"),
]


def parse_city(loc, title):
    """
    Tìm tên thành phố/tỉnh trong toàn bộ chuỗi location, chọn cái xuất hiện Ở VỊ TRÍ PHẢI NHẤT
    (tên thành phố luôn nằm cuối địa chỉ; tên đường như 'Hồ Chí Minh' hay phường 'Long An' nằm phía trước nên không lấn át).
    Không match 'chi minh' trần: 'Phường Chí Minh, Hải Dương' -> Hải Dương.
    """
    if pd.notna(loc) and str(loc).strip():
        t = re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", strip_accents(loc).lower())).strip()
        best, best_end = None, -1
        for label, rx in CITY_RX.items():
            for m in rx.finditer(t):
                if m.end() > best_end:
                    best, best_end = label, m.end()
        if best:
            return best, "location"
        if OVERSEAS_RE.search(t):
            return OVERSEAS, "location"
    # không thấy tên thành phố (vd chỉ 'Vietnam') -> thử tag trong title: [HN], (HCM), 'Tại Hà Nội'
    tt = plain(title)
    for city, pat in TITLE_CITY_TAGS:
        if re.search(pat, tt):
            return city, "title"
    return "Không rõ", "missing"


# --------------------------------------------------------------------------
# 2. Dedupe
# --------------------------------------------------------------------------
LEGAL_RE = re.compile(r"\b(cong ty|tnhh|co phan|cp|jsc|joint stock company|company limited|co|ltd|llc|inc|"
                      r"corp|corporation|pte|pty|vietnam|viet nam|vn|group|holdings?)\b")
LOC_TAG_RE = re.compile(r"[\[\(]\s*(hn|hcm|hcmc|ha noi|hanoi|ho chi minh|da nang|remote|hybrid|onsite)\s*[\]\)]", re.I)
SALARY_IN_TITLE_RE = re.compile(r"\b(offer\s+)?(up ?to|upto)\s*\$?[\d.,]+\s*[kmb$]*\w*", re.I)


def title_key(t):
    t = SALARY_IN_TITLE_RE.sub(" ", LOC_TAG_RE.sub(" ", str(t)))
    return plain(t)


def company_key(c):
    return re.sub(r"\s+", " ", LEGAL_RE.sub(" ", re.sub(r"[.,]", " ", plain(c)))).strip()


class UnionFind:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, a):
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


COALESCE_COLS = ["level", "salary_raw", "experience_raw", "deadline", "description_raw",
                 "requirements_raw", "skills_raw", "expertise_category", "location"]
FUZZY_RATIO = 0.90


def dedupe(df: pd.DataFrame):
    """
    Khóa trùng = (title_key, company_key, city)  -> gộp, giữ dòng đầy đủ nhất, điền field thiếu từ bản trùng.
    KHÔNG dùng job_id (ITviec job_id bị lặp: 'ITV_0004' gán cho nhiều job khác nhau) và KHÔNG dùng hash mô tả
    (mô tả LinkedIn nhiều job chỉ là đoạn giới thiệu công ty -> gộp nhầm). Cặp title gần giống (ratio>=0.90,
    cùng công ty + thành phố) chỉ được xuất ra file review, không tự gộp (Senior vs Lead, Android vs iOS là job khác nhau).
    """
    df = df.reset_index(drop=True)
    df["_tk"] = df["job_title"].map(title_key)
    df["_ck"] = df["company_name"].map(company_key)
    df["_grp"] = df.groupby(["_tk", "_ck", "city"], sort=False).ngroup()

    # cặp nghi trùng để review tay
    review = []
    for (ck, city), g in df.groupby(["_ck", "city"]):
        if len(g) < 2:
            continue
        idx, tks = g.index.tolist(), g["_tk"].tolist()
        for i in range(len(idx)):
            for j in range(i + 1, len(idx)):
                if tks[i] != tks[j]:
                    r = SequenceMatcher(None, tks[i], tks[j]).ratio()
                    if r >= FUZZY_RATIO:
                        review.append({"company": g.loc[idx[i], "company_name"], "city": city, "ratio": round(r, 3),
                                       "title_a": df.loc[idx[i], "job_title"], "url_a": df.loc[idx[i], "job_url"],
                                       "title_b": df.loc[idx[j], "job_title"], "url_b": df.loc[idx[j], "job_url"]})
    review = pd.DataFrame(review)

    df["_score"] = df[COALESCE_COLS].notna().sum(axis=1)
    df["_sp"] = df["source"].map(SOURCE_PRIORITY)
    df = df.sort_values(["_grp", "_score", "_sp", "crawled_at"], ascending=[True, False, True, False])

    rows = []
    for _, g in df.groupby("_grp", sort=False):
        base = g.iloc[0].copy()
        for c in COALESCE_COLS:
            if pd.isna(base[c]):
                nn = g[c].dropna()
                if len(nn):
                    base[c] = nn.iloc[0]
        base["n_duplicates"] = len(g) - 1
        base["sources_merged"] = "|".join(sorted(g["source"].unique(), key=SOURCE_PRIORITY.get))
        base["first_seen_at"] = g["crawled_at"].min()
        base["last_seen_at"] = g["crawled_at"].max()
        base["source_job_id"] = "|".join(g["job_id"].astype(str))          # giữ id gốc để truy vết
        # id ổn định giữa các lần chạy (dùng được cho --append): hash khóa trùng
        base["job_uid"] = hashlib.md5(f"{base['_tk']}|{base['_ck']}|{base['city']}".encode()).hexdigest()[:12]
        rows.append(base)
    out = pd.DataFrame(rows).drop(columns=["_tk", "_ck", "_grp", "_score", "_sp"]).reset_index(drop=True)
    return out, review


# --------------------------------------------------------------------------
# 3. Experience
# --------------------------------------------------------------------------
UNIT = r"(?:years?|yrs?|năm)"
RANGE_RE = re.compile(rf"(?<!\d)(\d{{1,2}})\s*(?:[-–—]|to|đến|tới)\s*(\d{{1,2}})(?!\d)\s*\+?\s*{UNIT}", re.I)
SINGLE_RE = re.compile(rf"(?<!\d)(\d{{1,2}})(?!\d)\s*(\+)?\s*{UNIT}", re.I)
EXP_CONTEXT_RE = re.compile(r"experience|kinh nghiệm|kinh nghiem|exp\b", re.I)
EXP_OUTLIER = 20          # ITviec/LinkedIn hay dính câu giới thiệu công ty ('30+ years', '115 năm') -> loại
TEXT_FALLBACK_MAX = 15    # trong JD text, số >=15 thường là tuổi công ty


def parse_exp_field(s):
    """experience_raw -> (min, max, is_plus, outlier). Chuỗi dài / không có 'year|năm' -> NaN."""
    if pd.isna(s):
        return np.nan, np.nan, False, False
    s = str(s)
    m = RANGE_RE.search(s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return min(lo, hi), max(lo, hi), "+" in s, False
    m = SINGLE_RE.search(s)
    if m:
        v = int(m.group(1))
        if v >= EXP_OUTLIER:
            return np.nan, np.nan, False, True
        return v, np.nan, bool(m.group(2)) or bool(re.search(r"trên|over|more than|at least|tối thiểu", s, re.I)), False
    if re.search(r"\d{3,}\s*" + UNIT, s, re.I):            # '115 NĂM'
        return np.nan, np.nan, False, True
    return np.nan, np.nan, False, False


def parse_exp_text(*texts):
    """Fallback: tìm 'N+ years of experience' / 'N năm kinh nghiệm' trong requirements > description > title."""
    for t in texts:
        if pd.isna(t):
            continue
        t = str(t)
        for rx in (RANGE_RE, SINGLE_RE):
            for m in rx.finditer(t):
                window = t[max(0, m.start() - 30): m.end() + 40]
                if not EXP_CONTEXT_RE.search(window):
                    continue
                lo = int(m.group(1))
                hi = int(m.group(2)) if rx is RANGE_RE else np.nan
                if lo >= TEXT_FALLBACK_MAX:
                    continue
                return lo, hi, rx is SINGLE_RE and ("+" in m.group(0))
    return np.nan, np.nan, False


# --------------------------------------------------------------------------
# 4. Level  (bảng ladder = "bảng chuẩn hóa level")
# --------------------------------------------------------------------------
LADDER = {0: "Intern", 1: "Fresher", 2: "Junior", 3: "Middle", 4: "Senior", 5: "Lead/Principal", 6: "Manager+"}
# nhãn nguồn -> (rank_min, rank_max, coarse?)   coarse = nhãn thô của LinkedIn, cho phép thu hẹp bằng title / số năm
SOURCE_LEVEL_MAP = {
    "intern": (0, 0, False), "fresher": (1, 1, False), "junior": (2, 2, False),
    "middle": (3, 3, False), "senior": (4, 4, False),
    "lead": (5, 5, False), "principal": (5, 5, False), "staff": (5, 5, False), "expert": (5, 5, False),
    "manager": (6, 6, False), "director": (6, 6, False),
    "entry level": (1, 2, True), "associate": (2, 3, True), "mid-senior level": (3, 4, True),
    # 'executive' (LinkedIn) mơ hồ (median 3.5 năm KN) -> không map, để title / số năm quyết định
}
TITLE_KW = [
    (0, r"\b(intern|internship|thuc tap)\b"),
    (1, r"\b(fresher|fresh graduate|fresh grad)s?\b"),
    (2, r"\b(junior|jr)\b"),
    (3, r"\b(middle|mid|mid level|intermediate)\b"),
    (4, r"\b(senior|sr)\b"),
    (5, r"\b(lead|leader|team lead|tech lead|principal|staff|expert|truong nhom)\b"),
    (6, r"\b(manager|head of|head|director|vp|chief|cto|cio|truong phong|giam doc)\b"),
]
# số năm KN -> level (chỉ dùng để SUY LUẬN khi không có gì khác; không bao giờ suy ra Manager)
EXP_BANDS = [(0, 1, 1), (1, 3, 2), (3, 5, 3), (5, 8, 4), (8, 99, 5)]   # [lo, hi) -> rank
XJOB_WEAK = {"fresher"}    # Xóm Jobs gán 'Fresher' tùy tiện (median 3 năm KN) -> chỉ tin khi không mâu thuẫn


def level_from_source(label):
    if pd.isna(label):
        return None
    parts = [p.strip().lower() for p in re.split(r"[|/]", str(label)) if p.strip()]
    mapped = [SOURCE_LEVEL_MAP[p] for p in parts if p in SOURCE_LEVEL_MAP]
    if not mapped:
        return None
    return min(m[0] for m in mapped), max(m[1] for m in mapped), all(m[2] for m in mapped), len(parts) > 1


def level_from_title(title):
    t = plain(title)
    ranks = [r for r, pat in TITLE_KW if re.search(pat, t)]
    return (min(ranks), max(ranks)) if ranks else None


def rank_from_exp(e):
    for lo, hi, r in EXP_BANDS:
        if lo <= e < hi:
            return r
    return None


def assign_level(row):
    src = level_from_source(row["level"])
    exp = row["exp_min"]
    # nhãn nguồn yếu (Xóm Jobs 'Fresher') bị bỏ nếu title / số năm mâu thuẫn
    if src and row["source"] == "XomJobs" and str(row["level"]).strip().lower() in XJOB_WEAK:
        if level_from_title(row["job_title"]) or (pd.notna(exp) and exp >= 2):
            src = None
    if src and not src[2]:                                   # nhãn cụ thể -> tin
        return src[0], src[1], "source"
    tk = level_from_title(row["job_title"])
    if tk:                                                   # title cụ thể hơn nhãn thô của LinkedIn
        return tk[0], tk[1], "title"
    if src:                                                  # nhãn thô LinkedIn: thu hẹp bằng số năm nếu có
        if pd.notna(exp):
            r = rank_from_exp(exp)
            if r is not None:
                r = min(max(r, src[0]), src[1])
                return r, r, "linkedin_coarse+exp"
        return src[0], src[1], "linkedin_coarse"
    if pd.notna(exp):
        r = rank_from_exp(exp)
        if r is not None:
            return r, r, "exp_band"
    return np.nan, np.nan, "unknown"


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="jobs_raw.xlsx")
    ap.add_argument("--outdir", default="clean_out")
    ap.add_argument("--as-of", default=None, help="ngày 'hôm nay' để cờ crawled_at bất thường (YYYY-MM-DD)")
    a = ap.parse_args()
    out = Path(a.outdir)
    out.mkdir(parents=True, exist_ok=True)
    as_of = pd.Timestamp(a.as_of) if a.as_of else pd.Timestamp.today().normalize()
    rep = []

    df = pd.read_excel(a.input) if a.input.endswith((".xlsx", ".xls")) else pd.read_csv(a.input)
    rep.append(f"Rows đầu vào: {len(df)}")

    # 0
    df = add_source(df)
    rep.append("[0] Nguồn: " + df["source"].value_counts().to_dict().__str__())

    # 1
    res = df.apply(lambda r: parse_city(r["location"], r["job_title"]), axis=1, result_type="expand")
    df["city"], df["city_source"] = res[0], res[1]
    rep.append("[1] City (trước dedupe): " + df["city"].value_counts().to_dict().__str__())
    rep.append("    city_source: " + df["city_source"].value_counts().to_dict().__str__())

    # 2
    n0 = len(df)
    df["location_raw"] = df["location"]
    df, review = dedupe(df)
    review.to_csv(out / "dedupe_review.csv", index=False, encoding="utf-8-sig")
    rep.append(f"[2] Dedupe: {n0} -> {len(df)} dòng (bỏ {n0 - len(df)}); "
               f"dòng có >=1 bản trùng: {(df['n_duplicates'] > 0).sum()}; "
               f"gộp chéo nguồn: {df['sources_merged'].str.contains(r'[|]').sum()}; "
               f"cặp title gần giống cần review tay: {len(review)} (dedupe_review.csv)")

    # 3
    p = df["experience_raw"].map(parse_exp_field)
    df["exp_min"] = [x[0] for x in p]
    df["exp_max"] = [x[1] for x in p]
    df["exp_is_plus"] = [x[2] for x in p]
    outlier = pd.Series([x[3] for x in p], index=df.index)
    df["exp_source"] = np.where(df["exp_min"].notna(), "experience_raw", None)
    miss = df["exp_min"].isna()
    t = df[miss].apply(lambda r: parse_exp_text(r["requirements_raw"], r["description_raw"], r["job_title"]),
                       axis=1, result_type="expand")
    df.loc[miss, "exp_min"], df.loc[miss, "exp_max"], df.loc[miss, "exp_is_plus"] = t[0], t[1], t[2]
    df.loc[miss & df["exp_min"].notna(), "exp_source"] = "jd_text"
    rep.append(f"[3] Exp: từ experience_raw {(df.exp_source == 'experience_raw').sum()}, "
               f"từ JD text {(df.exp_source == 'jd_text').sum()}, còn thiếu {df.exp_min.isna().sum()}; "
               f"outlier bị loại (>= {EXP_OUTLIER} năm): {int(outlier.sum())}")

    # 4
    lv = df.apply(assign_level, axis=1, result_type="expand")
    df["level_min"], df["level_max"], df["level_source"] = lv[0], lv[1], lv[2]
    df["level_primary"] = df["level_min"]                    # cận dưới: cùng ngữ nghĩa với exp_min
    df["is_level_range"] = df["level_min"].notna() & (df["level_min"] != df["level_max"])
    for c in ("level_primary", "level_min", "level_max"):
        df[c + "_label"] = df[c].map(LADDER)
    df["level_group"] = pd.cut(df["level_primary"], [-1, 2, 3, 4, 6], labels=["Entry", "Middle", "Senior", "Lead+"])
    conflict = ((df["level_source"] == "source") & df["exp_min"].notna() &
                (((df["level_max"] <= 2) & (df["exp_min"] >= 5)) | ((df["level_min"] >= 4) & (df["exp_min"] <= 1))))
    df["level_exp_conflict"] = conflict
    rep.append("[4] Level source: " + df["level_source"].value_counts().to_dict().__str__())
    rep.append("    Level primary: " + df["level_primary_label"].value_counts(dropna=False).to_dict().__str__())
    rep.append(f"    Level range (vd Middle|Senior): {int(df.is_level_range.sum())}; mâu thuẫn level↔exp cần rà: {int(conflict.sum())}")

    # 5  điền exp thiếu = median theo level (chỉ level 'source'/'title'; cột riêng, có cờ)
    ref = df[df["level_source"].isin(["source", "title"]) & df["exp_min"].notna()]
    med = ref.groupby("level_primary")["exp_min"].median()
    df["exp_min_filled"] = df["exp_min"]
    fill = df["exp_min"].isna() & df["level_source"].isin(["source", "title"])
    df.loc[fill, "exp_min_filled"] = df.loc[fill, "level_primary"].map(med)
    df.loc[fill & df["exp_min_filled"].notna(), "exp_source"] = "level_median"
    rep.append("[5] Median exp theo level (dùng để điền): " + med.rename(LADDER).round(1).to_dict().__str__())
    rep.append(f"    exp_min_filled còn thiếu: {df.exp_min_filled.isna().sum()}; "
               f"cả level lẫn exp đều thiếu: {(df.level_primary.isna() & df.exp_min.isna()).sum()}")

    # cảnh báo khác
    fut = (df["crawled_at"] > as_of + pd.Timedelta(days=1)).sum()
    rep.append(f"[!] crawled_at: {df.crawled_at.min()} -> {df.crawled_at.max()}; "
               f"{fut} dòng có crawled_at sau {as_of.date()} (kiểm tra đồng hồ máy crawl)")
    rep.append(f"[!] expertise_category trống: {df.expertise_category.isna().sum()}; "
               f"skills_raw trống: {df.skills_raw.isna().sum()}; city 'Không rõ': {(df.city == 'Không rõ').sum()}")

    cols = ["job_uid", "source_job_id", "source", "job_title", "company_name", "city", "city_source",
            "level_primary", "level_primary_label", "level_min_label", "level_max_label", "level_group",
            "is_level_range", "level_source", "level_exp_conflict",
            "exp_min", "exp_max", "exp_is_plus", "exp_source", "exp_min_filled",
            "salary_raw", "expertise_category", "skills_raw", "requirements_raw", "description_raw",
            "job_url", "location_raw", "updated_at", "deadline", "crawled_at",
            "first_seen_at", "last_seen_at", "n_duplicates", "sources_merged"]
    df[cols].to_csv(out / "jobs_clean.csv", index=False, encoding="utf-8-sig")

    lm = [{"source_label": k, "rank_min": v[0], "rank_max": v[1], "is_coarse": v[2],
           "label_min": LADDER[v[0]], "label_max": LADDER[v[1]]} for k, v in SOURCE_LEVEL_MAP.items()]
    pd.DataFrame(lm).to_csv(out / "level_map.csv", index=False, encoding="utf-8-sig")
    (out / "cleaning_report.txt").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    print(f"\n-> {out/'jobs_clean.csv'} ({len(df)} dòng)")


if __name__ == "__main__":
    main()
