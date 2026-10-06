#!/usr/bin/env python3
"""
rq3_pooled.py - RQ3 theo phương án mô tả gộp + kiểm định phi tham số.
"Ở cùng một mốc kinh nghiệm, độ rộng kỹ năng (n_hard_skills) và độ phức tạp tech stack (n_skill_groups)
 khác nhau thế nào giữa Frontend, Backend, Data Engineer?"

Quy trình:
  1. Bảng mô tả gộp nguồn: nhóm nghề x mốc kinh nghiệm -> n, trung bình, trung vị (+ cơ cấu nguồn của từng ô).
  2. Kruskal-Wallis cho từng mốc (chỉ mốc mà MỌI nhóm nghề có >= --min-n job), hiệu chỉnh Holm giữa các mốc.
  3. Hậu kiểm Dunn từng cặp nghề (hiệu chỉnh Holm trong mốc) cho mốc có KW ý nghĩa trước hiệu chỉnh.
  4. Kiểm tra độ nhạy: lặp lại KW chỉ trên ITviec (nguồn duy nhất có cả 3 nhóm nghề; Frontend ở đó rất ít job nên kiểm định yếu). Chỉ để xem kết luận
     có còn giữ khi loại ảnh hưởng của nguồn hay không, không phải so sánh giữa các nguồn.
Chỉ dùng exp_min gốc (không dùng exp_min_filled) để tránh vòng tròn level <-> kinh nghiệm.

Chạy: python rq3_pooled.py --input jd_skill_binary.csv --outdir results/rq3_out
"""
import argparse
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

ROLES = ["Backend", "Frontend", "Data Engineer"]
BANDS = [(0, 1, "Fresher (<1 năm)"), (1, 3, "Junior (1-2 năm)"), (3, 5, "Middle (3-4 năm)"),
         (5, 8, "Senior (5-7 năm)"), (8, 99, "Lead+ (>=8 năm)")]
BAND_ORDER = [b[2] for b in BANDS]


def band_of(e):
    return next((l for lo, hi, l in BANDS if lo <= e < hi), None) if pd.notna(e) else None


def holm(p):
    p = np.asarray(p, float)
    m = len(p)
    order = np.argsort(p)
    adj = np.maximum.accumulate((m - np.arange(m)) * p[order])
    out = np.empty(m)
    out[order] = np.minimum(adj, 1)
    return out


def dunn(groups):
    """Dunn (có hiệu chỉnh đồng hạng) cho từng cặp; trả về list (i, j, z, p_thô)."""
    allv = np.concatenate(groups)
    N = len(allv)
    ranks = stats.rankdata(allv)
    _, counts = np.unique(allv, return_counts=True)
    sigma2 = N * (N + 1) / 12 - (counts**3 - counts).sum() / (12 * (N - 1))
    idx = np.cumsum([0] + [len(g) for g in groups])
    rbar = [ranks[idx[k]:idx[k + 1]].mean() for k in range(len(groups))]
    out = []
    for i, j in combinations(range(len(groups)), 2):
        z = (rbar[i] - rbar[j]) / np.sqrt(sigma2 * (1 / len(groups[i]) + 1 / len(groups[j])))
        out.append((i, j, z, 2 * stats.norm.sf(abs(z))))
    return out


def kw(sub, roles, metric, min_n):
    gr = [sub.loc[sub.expertise_category == r, metric].to_numpy() for r in roles]
    if any(len(x) < min_n for x in gr):
        return None, gr
    return stats.kruskal(*gr), gr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="jd_skill_binary.csv")
    ap.add_argument("--outdir", default="results/rq3_out")
    ap.add_argument("--roles", default=",".join(ROLES))
    ap.add_argument("--min-n", type=int, default=10, help="mỗi nhóm nghề cần >= bấy nhiêu job trong mốc mới được kiểm định")
    ap.add_argument("--min-n-sens", type=int, default=5, help="ngưỡng thấp hơn cho kiểm tra độ nhạy ITviec-only (Frontend ở ITviec rất ít job, kiểm định yếu)")
    a = ap.parse_args()
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(a.input) if a.input.endswith((".xlsx", ".xls")) else pd.read_csv(a.input, dtype={"job_uid": str})
    base = df[df.expertise_category.isin(roles)]
    g = base[base.exp_min.notna() & (base.has_skills == 1)].copy()
    g["band"] = g.exp_min.map(band_of)
    n_excl = len(base) - len(g)

    # 1) bảng mô tả gộp
    rows = []
    for r in roles:
        for b in BAND_ORDER:
            c = g[(g.expertise_category == r) & (g.band == b)]
            if c.empty:
                continue
            mix = c.source.value_counts(normalize=True).mul(100).round(0)
            rows.append({"role": r, "band": b, "n_jobs": len(c),
                         "n_hard_skills_mean": round(c.n_hard_skills.mean(), 1), "n_hard_skills_median": c.n_hard_skills.median(),
                         "n_skill_groups_mean": round(c.n_skill_groups.mean(), 1),
                         **{f"pct_{s}": mix.get(s, 0.0) for s in sorted(g.source.unique())}})
    table = pd.DataFrame(rows)
    table.to_csv(out / "rq3_pooled_table.csv", index=False, encoding="utf-8-sig")

    # 2) Kruskal-Wallis từng mốc + 4) độ nhạy ITviec
    tests, post = [], []
    for metric in ("n_hard_skills", "n_skill_groups"):
        t_rows = []
        for b in BAND_ORDER:
            sub = g[g.band == b]
            res, gr = kw(sub, roles, metric, a.min_n)
            res_it, gr_it = kw(sub[sub.source == "ITviec"], roles, metric, a.min_n_sens)
            t_rows.append({"metric": metric, "band": b, **{f"n_{r}": len(x) for r, x in zip(roles, gr)},
                           "H": round(res[0], 2) if res else np.nan, "p": round(res[1], 4) if res else np.nan,
                           **{f"n_ITviec_{r}": len(x) for r, x in zip(roles, gr_it)},
                           "H_ITviec": round(res_it[0], 2) if res_it else np.nan, "p_ITviec": round(res_it[1], 4) if res_it else np.nan,
                           "_gr": gr, "_sub": sub})
        ok = [i for i, r in enumerate(t_rows) if not np.isnan(r["p"])]
        if ok:
            adj = holm([t_rows[i]["p"] for i in ok])
            for i, pa in zip(ok, adj):
                t_rows[i]["p_holm_across_bands"] = round(pa, 4)
        for r in t_rows:
            r.setdefault("p_holm_across_bands", np.nan)
            if not np.isnan(r["p"]) and r["p"] < 0.05:  # hậu kiểm cho mốc có KW p<0.05
                d = dunn(r["_gr"])
                ph = holm([x[3] for x in d])
                for (i, j, z, p), pa in zip(d, ph):
                    post.append({"metric": metric, "band": r["band"], "role_a": roles[i], "role_b": roles[j], "mean_a": round(r["_gr"][i].mean(), 1),
                                 "mean_b": round(r["_gr"][j].mean(), 1), "z": round(z, 2), "p": round(p, 4), "p_holm": round(pa, 4)})
        tests += [{k: v for k, v in r.items() if not k.startswith("_")} for r in t_rows]
    tests_df, post_df = pd.DataFrame(tests), pd.DataFrame(post)
    tests_df.to_csv(out / "rq3_pooled_tests.csv", index=False, encoding="utf-8-sig")
    post_df.to_csv(out / "rq3_pooled_posthoc.csv", index=False, encoding="utf-8-sig")

    # report
    rep = [f"Vị trí: {', '.join(roles)} | chỉ dùng exp_min gốc | loại {n_excl} job thiếu exp_min gốc hoặc không có skill",
           f"Kiểm định mốc nào mà mọi nhóm nghề có >= {a.min_n} job.\n", "=== Bảng mô tả gộp: n_hard_skills trung bình [n] ==="]
    piv = table.assign(v=table.n_hard_skills_mean.astype(str) + " [" + table.n_jobs.astype(str) + "]").pivot(index="role", columns="band", values="v")
    rep.append(piv.reindex(columns=[b for b in BAND_ORDER if b in piv.columns]).fillna("-").to_string())
    rep.append("\n=== Cơ cấu nguồn của từng ô (% job) ===")
    src_cols = [c for c in table.columns if c.startswith("pct_")]
    rep.append(table[["role", "band", "n_jobs", *src_cols]].to_string(index=False))
    for metric in ("n_hard_skills", "n_skill_groups"):
        rep.append(f"\n=== Kruskal-Wallis theo mốc: {metric} ===")
        for r in tests_df[tests_df.metric == metric].itertuples():
            if np.isnan(r.p):
                rep.append(f"  {r.band:<18} không đủ mẫu"); continue
            it = f"ITviec-only p={r.p_ITviec}" if not np.isnan(r.p_ITviec) else "ITviec-only: không đủ mẫu"
            rep.append(f"  {r.band:<18} H={r.H:>5}  p={r.p}  p_Holm(giữa các mốc)={r.p_holm_across_bands}  | {it}")
    if len(post_df):
        rep.append("\n=== Hậu kiểm Dunn (mốc có KW p<0.05, hiệu chỉnh Holm trong mốc) ===")
        rep.append(post_df.to_string(index=False))
    rep.append("\nLưu ý: nhóm nghề và nguồn gần như trùng nhau (Backend 100% ITviec; Frontend ~78% LinkedIn; Data Engineer ~70% XomJobs), "
               "nên khác biệt khi gộp có thể phản ánh cách mỗi nguồn ghi skill. Đối chiếu cột ITviec-only trước khi diễn giải.")
    (out / "rq3_pooled_report.txt").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))


if __name__ == "__main__":
    main()
