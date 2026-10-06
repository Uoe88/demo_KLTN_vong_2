"""analytics.py - lớp phân tích cho dashboard kỹ năng (tần suất, độ phủ, đồng xuất hiện).

Thuật ngữ: dùng nhất quán "phân tích đồng xuất hiện / tổ hợp kỹ năng". Không khai phá luật kết hợp
(không Apriori/FP-growth), nên không dùng các từ "support/confidence/luật kết hợp".
"""
import re

import numpy as np
import pandas as pd

try:
    from scipy.stats import fisher_exact
except Exception:  # scipy không bắt buộc: thiếu thì bỏ cột p/q
    fisher_exact = None

VALID_UID = re.compile(r"^[0-9a-f]{12}$")  # job_uid hợp lệ: 12 ký tự hex
LANG_SLUGS = {"english", "japanese", "french"}
ROLE_ALIASES = ("expertise_category", "role", "nhom_nghe")
PLATFORM_ALIASES = ("platform", "source", "nguon")
DATE_ALIASES = ("posted_date", "date", "ngay_dang", "updated_at")  # KHÔNG dùng crawled_at: đã hỏng (mm:ss.0)
PAIR_COLS = ["skill_a", "skill_b", "n_a", "n_b", "n_both", "pct_both_of_role", "p_b_given_a",
             "p_a_given_b", "lift", "jaccard", "npmi", "p_value", "q_value", "reliable"]


# ---------- nạp dữ liệu ----------
def slugify(name: str) -> str:
    s = name.lower().replace("c#", "csharp").replace("c++", "cpp").replace(".net", "dotnet")
    return re.sub(r"[^a-z0-9]+", "_", s).strip("_")


def _standardize(df: pd.DataFrame) -> pd.DataFrame:
    """Thêm cột chuẩn (nhóm nghề / nền tảng / ngày đăng) từ các tên cột có thể gặp và parse ngày (dd/mm/yyyy)."""
    for target, aliases in (("expertise_category", ROLE_ALIASES), ("platform", PLATFORM_ALIASES),
                            ("posted_date", DATE_ALIASES)):
        if target in df.columns:
            continue
        hit = next((c for c in aliases if c in df.columns), None)
        if hit:
            df[target] = df[hit]  # giữ nguyên cột gốc (rq1..rq4 vẫn đọc 'source')
    if "posted_date" in df.columns:
        df["posted_date"] = pd.to_datetime(df["posted_date"], errors="coerce", dayfirst=True)
    return df


def prepare(df: pd.DataFrame):
    """Chuẩn hoá DataFrame đã ghép: thêm cột chuẩn, ép cột skill_* về bool. Trả về (df, skill_cols)."""
    df = _standardize(df)
    skill_cols = [c for c in df.columns if c.startswith("skill_")]
    df[skill_cols] = df[skill_cols].fillna(0).astype(bool)
    return df, skill_cols


def load_binary(src):
    """Đọc job_skill_binary.csv hoặc jd_skill_binary.csv (đã ghép metadata)."""
    return prepare(pd.read_csv(src, dtype={"job_uid": str}))


def load_meta(src) -> pd.DataFrame:
    """File phụ: job_uid + nhóm nghề (+ nền tảng, ngày đăng nếu có)."""
    m = _standardize(pd.read_csv(src, dtype={"job_uid": str}))
    keep = ["job_uid"] + [c for c in ("expertise_category", "platform", "posted_date") if c in m.columns]
    return m[keep].drop_duplicates("job_uid")


def bad_job_uid(df: pd.DataFrame) -> pd.Series:
    """job_uid hỏng (vd 4.22E+11 do Excel đổi sang số khoa học): không phải 12 ký tự hex."""
    return ~df["job_uid"].astype(str).str.fullmatch(VALID_UID)


def skill_info(skill_cols, taxonomy: pd.DataFrame | None = None) -> pd.DataFrame:
    """Nhãn đẹp + group cho từng cột skill_*. Có taxonomy thì dùng, không thì suy từ tên cột."""
    info = pd.DataFrame({"col": skill_cols})
    info["slug"] = info["col"].str[6:]
    info["label"] = info["slug"].str.replace("_", " ")
    info["group"] = "Khác"
    if taxonomy is not None and len(taxonomy):
        t = taxonomy.copy()
        t["slug"] = t["canonical"].map(slugify)
        t = t.drop_duplicates("slug").set_index("slug")
        hit = info["slug"].isin(t.index)
        info.loc[hit, "label"] = info.loc[hit, "slug"].map(t["canonical"])
        info.loc[hit, "group"] = info.loc[hit, "slug"].map(t["group"])
        cert = info["slug"] == "cert_security"  # CISSP/CEH/OSCP/CISM đã gộp thành 1 cột
        info.loc[cert, ["label", "group"]] = ["Security certification (CISSP/CEH/OSCP/CISM)", "Certification"]
    return info.set_index("col")


# ---------- chọn tập job ----------
def select_jobs(df: pd.DataFrame, only_hard: bool = False) -> pd.DataFrame:
    mask = df["skills_status"].eq("has_hard") if only_hard else df["has_skills"].eq(1)
    return df[mask]


# ---------- tần suất ----------
def wilson(k, n, z=1.96):
    k = np.asarray(k, float)
    if n == 0:
        return np.zeros_like(k), np.zeros_like(k)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return 100 * (centre - half), 100 * (centre + half)


def skill_frequency(g: pd.DataFrame, cols) -> pd.DataFrame:
    n = len(g)
    cnt = g[cols].sum().astype(int)
    out = pd.DataFrame({"skill": cols, "n_jobs": cnt.values})
    out["pct"] = 100 * out["n_jobs"] / n if n else 0.0
    out["ci_low"], out["ci_high"] = wilson(out["n_jobs"], n)
    return out.sort_values(["n_jobs", "skill"], ascending=[False, True]).reset_index(drop=True)


def role_skill_matrix(df: pd.DataFrame, cols, role_col="expertise_category") -> pd.DataFrame:
    """% job của mỗi nhóm nghề yêu cầu skill (hàng = skill, cột = nhóm nghề)."""
    return (df.groupby(role_col)[list(cols)].mean() * 100).T


def lift_vs_overall(df: pd.DataFrame, cols, role_col="expertise_category") -> pd.DataFrame:
    """Tỉ lệ % trong nhóm nghề / % toàn bộ: >1 nghĩa là skill đặc trưng hơn cho nhóm nghề đó."""
    overall = df[list(cols)].mean() * 100
    m = role_skill_matrix(df, cols, role_col)
    return m.div(overall.replace(0, np.nan), axis=0)


# ---------- độ phủ ----------
def group_coverage(df: pd.DataFrame, info: pd.DataFrame, role_col="expertise_category") -> pd.DataFrame:
    """% job của nhóm nghề có ít nhất 1 skill thuộc nhóm kỹ năng (hàng = nhóm nghề)."""
    res = {grp: df[list(sub.index)].any(axis=1).groupby(df[role_col]).mean() * 100
           for grp, sub in info.groupby("group")}
    return pd.concat(res, axis=1)


def topk_coverage(g: pd.DataFrame, cols, kmax=15) -> pd.DataFrame:
    """Nếu học top-k skill phổ biến nhất của nhóm nghề này thì bao nhiêu % job được phủ?
    any1: job yêu cầu >= 1 skill trong top-k; half: job yêu cầu >= nửa số skill trong top-k."""
    order = skill_frequency(g, cols)["skill"].tolist()[:kmax]
    n = len(g)
    rows = []
    for k in range(1, len(order) + 1):
        hits = g[order[:k]].sum(axis=1)
        rows.append({"k": k, "skill_them": order[k - 1],
                     "any1": 100 * (hits >= 1).mean() if n else 0.0,
                     "half": 100 * (hits >= int(np.ceil(k / 2))).mean() if n else 0.0})
    return pd.DataFrame(rows)


# ---------- đồng xuất hiện ----------
def _bh(p):
    p = np.asarray(p, float)
    m = len(p)
    order = np.argsort(p)
    q = np.minimum.accumulate((p[order] * m / np.arange(1, m + 1))[::-1])[::-1]
    out = np.empty(m)
    out[order] = np.minimum(q, 1)
    return out


def cooccurrence(g: pd.DataFrame, cols, min_skill_pct=5.0, min_pair_n=5, with_fisher=True) -> pd.DataFrame:
    n = len(g)
    if n == 0:
        return pd.DataFrame(columns=PAIR_COLS)
    freq = g[list(cols)].mean() * 100
    sel = [c for c in cols if freq[c] >= min_skill_pct]
    if len(sel) < 2:
        return pd.DataFrame(columns=PAIR_COLS)
    X = g[sel].to_numpy(dtype=np.int64)
    co = X.T @ X
    cnt = np.diag(co)
    a, b = np.triu_indices(len(sel), 1)
    n_a, n_b, n_ab = cnt[a], cnt[b], co[a, b]
    p_a, p_b, p_ab = n_a / n, n_b / n, n_ab / n
    with np.errstate(divide="ignore", invalid="ignore"):
        lift = p_ab / (p_a * p_b)
        jacc = n_ab / (n_a + n_b - n_ab)
        npmi = np.where(n_ab > 0, np.log(lift) / -np.log(p_ab), -1.0)
        npmi = np.where(p_ab >= 1, 1.0, npmi)
    out = pd.DataFrame({
        "skill_a": np.array(sel)[a], "skill_b": np.array(sel)[b],
        "n_a": n_a, "n_b": n_b, "n_both": n_ab,
        "pct_both_of_role": np.round(100 * p_ab, 1),
        "p_b_given_a": np.round(100 * n_ab / n_a, 1),  # P(B|A): % job có A mà cũng có B
        "p_a_given_b": np.round(100 * n_ab / n_b, 1),  # P(A|B)
        "lift": np.round(lift, 2), "jaccard": np.round(jacc, 3), "npmi": np.round(npmi, 3),
    })
    out["reliable"] = out["n_both"] >= min_pair_n
    out["p_value"] = np.nan
    out["q_value"] = np.nan
    if with_fisher and fisher_exact is not None:
        p = [fisher_exact([[ab, aa - ab], [bb - ab, n - aa - bb + ab]], alternative="greater")[1]
             for aa, bb, ab in zip(n_a, n_b, n_ab)]
        out["p_value"] = p
        out["q_value"] = _bh(p)  # hiệu chỉnh Benjamini-Hochberg trên tất cả cặp đã xét
    return out[PAIR_COLS]


def find_clusters(pairs: pd.DataFrame, lift_threshold=2.5, min_n=5, q_max=None) -> pd.DataFrame:
    """Cụm đồng xuất hiện: thành phần liên thông (single-linkage) trên các cặp lift cao.
    Ngưỡng lift thấp (<2) dễ nối mọi skill thành 1 cụm lớn."""
    if pairs.empty:
        return pd.DataFrame(columns=["cluster_id", "n_skills", "skills"])
    s = pairs[(pairs.lift >= lift_threshold) & (pairs.n_both >= min_n)]
    if q_max is not None and s["q_value"].notna().any():
        s = s[s["q_value"] <= q_max]
    nodes = sorted(set(s.skill_a) | set(s.skill_b))
    parent = {x: x for x in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for r in s.itertuples():
        ra, rb = find(r.skill_a), find(r.skill_b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups = {}
    for x in nodes:
        groups.setdefault(find(x), []).append(x)
    rows = [{"n_skills": len(m), "skills": sorted(m)} for m in groups.values() if len(m) >= 2]
    rows.sort(key=lambda r: -r["n_skills"])
    return pd.DataFrame([{"cluster_id": i, **r} for i, r in enumerate(rows, 1)],
                        columns=["cluster_id", "n_skills", "skills"])


def lift_matrix(pairs: pd.DataFrame, top_skills) -> pd.DataFrame:
    """Ma trận lift đối xứng cho các skill trong top_skills (để vẽ heatmap)."""
    sub = pairs[pairs.skill_a.isin(top_skills) & pairs.skill_b.isin(top_skills)]
    m = sub.pivot(index="skill_a", columns="skill_b", values="lift")
    m = m.reindex(index=top_skills, columns=top_skills)
    return m.combine_first(m.T)
