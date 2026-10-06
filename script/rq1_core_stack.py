#!/usr/bin/env python3
"""
rq1_core_stack.py  -  Câu hỏi nghiên cứu 1
"Tổ hợp các kỹ năng chuyên môn bắt buộc (Core Stack) đại diện cho từng vị trí
 (Backend, Frontend, Data Engineer, Data Scientist, AI Engineer) gồm những công
 nghệ nào, và có sự khác biệt gì về độ rộng kỹ năng?"

Input:  jd_skill_binary.xlsx/.csv  (2.558 dòng x 120 cột: metadata + has_skills/
        n_hard_skills/n_skill_groups + 103 cột skill_*)
Output (trong --outdir):
    rq1_core_stack.csv       vị trí x skill: n, %, khoảng tin cậy 95%, có phải core không
    rq1_breadth.csv          vị trí x (tổng thể + theo nguồn): n_hard_skills, n_skill_groups
    rq1_breadth_test.csv     kiểm định khác biệt độ rộng giữa các vị trí (Kruskal-Wallis
                              trên toàn bộ + pairwise Mann-Whitney, mỗi vị trí RIÊNG theo nguồn)
    rq1_report.txt           tóm tắt số liệu + các điểm cần đọc kèm cảnh báo cỡ mẫu

Cách chạy:
    python rq1_core_stack.py --input jd_skill_binary.xlsx --outdir rq1_out

Hai lưu ý quan trọng về cỡ mẫu (đọc kèm rq1_report.txt trước khi dùng kết quả):
  1. Cỡ mẫu giữa 5 vị trí lệch nhau rất nhiều (ví dụ Data Scientist ~68 job so với
     AI Engineer ~340 job). Script tính khoảng tin cậy Wilson 95% cho MỌI tỷ lệ %,
     và gắn cờ core_reliable=False cho các vị trí có n < --min-n (mặc định 50) --
     tỷ lệ % của các vị trí này vẫn được báo cáo nhưng PHẢI đọc kèm khoảng tin cậy,
     không nên khẳng định chắc chắn thứ hạng kỹ năng.
  2. Độ rộng kỹ năng (n_hard_skills) bị lệch theo NGUỒN tuyển dụng chứ không chỉ theo
     vị trí (ITviec ghi ít skill hơn Xóm Jobs rõ rệt). Vì vậy script KHÔNG so sánh độ
     rộng thẳng giữa các vị trí khi chúng không cùng cơ cấu nguồn -- rq1_breadth.csv
     luôn tách riêng theo nguồn, và rq1_breadth_test.csv chỉ so sánh hai vị trí trên
     cùng một nguồn (nguồn nào không đủ job ở CẢ HAI vị trí thì bỏ qua cặp đó).
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

TARGET_ROLES = ["Backend", "Frontend", "Data Engineer", "Data Scientist", "AI Engineer"]
MIN_N_BREADTH = 15   # dưới ngưỡng này, trung bình n_hard_skills của 1 ô (vị trí x nguồn) không đáng tin, chỉ để tham khảo


def wilson_ci(k, n, z=1.96):
    """Khoảng tin cậy Wilson 95% cho một tỷ lệ -- ổn định hơn CI chuẩn khi n nhỏ hoặc
    tỷ lệ gần 0%/100% (cả hai tình huống đều xảy ra ở nhóm nghề nhỏ như Data Scientist)."""
    if n == 0:
        return np.nan, np.nan
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = (z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def role_skill_table(df, skill_cols, roles, min_n):
    """Với mỗi vị trí: n job có skills_raw (mẫu số), % mỗi skill, CI 95%, cờ core/đáng tin."""
    rows = []
    for role in roles:
        g = df[(df.expertise_category == role) & (df.has_skills == 1)]
        n = len(g)
        reliable = n >= min_n
        for col in skill_cols:
            k = int(g[col].sum())
            pct = 100 * k / n if n else np.nan
            lo, hi = wilson_ci(k, n)
            rows.append({
                "role": role, "n_jobs_with_skills": n, "skill": col,
                "n": k, "pct": round(pct, 1) if n else np.nan,
                "ci_low_pct": round(100 * lo, 1) if n else np.nan,
                "ci_high_pct": round(100 * hi, 1) if n else np.nan,
                "role_sample_reliable": reliable,
                "is_core": bool(n and pct >= 30),       # >=30% job trong vị trí yêu cầu -> coi là "bắt buộc"
            })
    out = pd.DataFrame(rows).sort_values(["role", "pct"], ascending=[True, False])
    return out


def breadth_by_role_source(df, roles):
    """Độ rộng kỹ năng (n_hard_skills, n_skill_groups) theo vị trí, LUÔN tách theo nguồn
    vì nguồn ảnh hưởng mạnh đến số skill ghi trong tin (xem docstring)."""
    g = df[df.expertise_category.isin(roles) & (df.has_skills == 1)]
    agg = g.groupby(["role" if False else "expertise_category", "source"]).agg(
        n_jobs=("n_hard_skills", "size"),
        n_hard_skills_mean=("n_hard_skills", "mean"),
        n_hard_skills_median=("n_hard_skills", "median"),
        n_skill_groups_mean=("n_skill_groups", "mean"),
    ).reset_index().rename(columns={"expertise_category": "role"})
    agg[["n_hard_skills_mean", "n_skill_groups_mean"]] = agg[["n_hard_skills_mean", "n_skill_groups_mean"]].round(1)
    agg["cell_reliable"] = agg.n_jobs >= MIN_N_BREADTH
    # tổng theo vị trí (gộp nguồn) -- CHỈ để tham khảo quy mô chung, không dùng để so sánh vị trí
    overall = g.groupby("expertise_category").agg(
        n_jobs=("n_hard_skills", "size"),
        n_hard_skills_mean=("n_hard_skills", "mean"),
        n_hard_skills_median=("n_hard_skills", "median"),
        n_skill_groups_mean=("n_skill_groups", "mean"),
    ).reset_index().rename(columns={"expertise_category": "role"})
    overall["source"] = "TẤT CẢ NGUỒN (tham khảo, không dùng để so sánh vị trí)"
    overall[["n_hard_skills_mean", "n_skill_groups_mean"]] = overall[["n_hard_skills_mean", "n_skill_groups_mean"]].round(1)
    overall["cell_reliable"] = True
    return pd.concat([overall, agg], ignore_index=True).sort_values(["role", "source"])


def breadth_tests(df, roles, min_n_per_cell=15):
    """So sánh độ rộng kỹ năng giữa các vị trí:
       (a) Kruskal-Wallis tổng thể trên toàn bộ job của 5 vị trí (không tách nguồn --
           chỉ để biết CÓ khác biệt tổng quát hay không, không kết luận vị trí nào hơn).
       (b) Mann-Whitney từng cặp vị trí, NHƯNG CHỈ trong cùng một nguồn, và chỉ khi cả
           hai vị trí có >= min_n_per_cell job trên nguồn đó -- tránh so sánh bị nhiễu
           bởi cách ghi skill khác nhau giữa các nguồn.
    """
    g = df[df.expertise_category.isin(roles) & (df.has_skills == 1)]
    rows = []
    groups_all = [g.loc[g.expertise_category == r, "n_hard_skills"] for r in roles]
    if all(len(x) > 0 for x in groups_all):
        h, p = stats.kruskal(*groups_all)
        rows.append({"test": "Kruskal-Wallis (5 vị trí, gộp mọi nguồn -- chỉ kiểm tra có khác biệt tổng quát)",
                     "role_a": "-", "role_b": "-", "source": "tất cả",
                     "n_a": "-", "n_b": "-", "statistic": round(h, 2), "p_value": round(p, 4),
                     "significant_0.05": bool(p < 0.05)})
    for src in g.source.unique():
        gs = g[g.source == src]
        for i in range(len(roles)):
            for j in range(i + 1, len(roles)):
                a, b = roles[i], roles[j]
                xa = gs.loc[gs.expertise_category == a, "n_hard_skills"]
                xb = gs.loc[gs.expertise_category == b, "n_hard_skills"]
                if len(xa) < min_n_per_cell or len(xb) < min_n_per_cell:
                    continue
                u, p = stats.mannwhitneyu(xa, xb, alternative="two-sided")
                rows.append({"test": "Mann-Whitney (trong cùng 1 nguồn)", "role_a": a, "role_b": b, "source": src,
                             "n_a": len(xa), "n_b": len(xb), "statistic": round(u, 1), "p_value": round(p, 4),
                             "significant_0.05": bool(p < 0.05)})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="jd_skill_binary.xlsx")
    ap.add_argument("--outdir", default="rq1_out")
    ap.add_argument("--roles", default=",".join(TARGET_ROLES))
    ap.add_argument("--min-n", type=int, default=50, help="dưới ngưỡng này, % của skill trong vị trí được coi là KHÔNG đủ tin cậy")
    ap.add_argument("--core-threshold", type=float, default=30.0, help="%% job tối thiểu trong vị trí để coi 1 skill là 'core stack' (mặc định 30%%)")
    ap.add_argument("--top-n", type=int, default=12, help="số skill core hiển thị trong report cho mỗi vị trí")
    a = ap.parse_args()
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(a.input) if a.input.endswith((".xlsx", ".xls")) else pd.read_csv(a.input)
    LANGUAGE_SKILL_COLS = {"skill_english", "skill_japanese", "skill_french"}   # từ bản build_skill_taxonomy.py mới,
    # ngoại ngữ nằm chung tiền tố skill_* với kỹ năng chuyên môn -- loại ra khỏi phân tích "core stack"/"tổ hợp
    # kỹ năng" của câu hỏi này (ngoại ngữ đã được trả lời riêng ở rq2_density.py, mục "Soft skill: ...")
    skill_cols = [c for c in df.columns if c.startswith("skill_") and c not in LANGUAGE_SKILL_COLS]
    missing_roles = [r for r in roles if r not in df.expertise_category.unique()]
    if missing_roles:
        raise SystemExit(f"Không tìm thấy vị trí trong expertise_category: {missing_roles}")

    tax = role_skill_table(df, skill_cols, roles, a.min_n)
    tax["is_core"] = tax.pct >= a.core_threshold
    tax.to_csv(out / "rq1_core_stack.csv", index=False, encoding="utf-8-sig")

    breadth = breadth_by_role_source(df, roles)
    breadth.to_csv(out / "rq1_breadth.csv", index=False, encoding="utf-8-sig")

    tests = breadth_tests(df, roles)
    tests.to_csv(out / "rq1_breadth_test.csv", index=False, encoding="utf-8-sig")

    # ---------------- report ----------------
    rep = [f"Vị trí mục tiêu: {', '.join(roles)}", f"Ngưỡng 'core stack': skill xuất hiện >= {a.core_threshold:.0f}% job trong vị trí",
           f"Ngưỡng cỡ mẫu tin cậy: n >= {a.min_n} job có skills_raw", ""]

    rep.append("=== Cỡ mẫu từng vị trí (job có skills_raw) ===")
    for role in roles:
        n = int(tax.loc[tax.role == role, "n_jobs_with_skills"].iloc[0])
        flag = "" if n >= a.min_n else "  <-- DƯỚI NGƯỠNG TIN CẬY, đọc kèm khoảng tin cậy (CI) trong rq1_core_stack.csv"
        rep.append(f"  {role:<16} n = {n}{flag}")

    rep.append("\n=== Core stack mỗi vị trí (top skill theo %, kèm CI 95%) ===")
    for role in roles:
        sub = tax[(tax.role == role) & (tax.is_core)].sort_values("pct", ascending=False).head(a.top_n)
        reliable = tax.loc[tax.role == role, "role_sample_reliable"].iloc[0]
        rep.append(f"\n{role}{'  [CẢNH BÁO: n nhỏ, % có thể dao động mạnh]' if not reliable else ''}")
        for r in sub.itertuples():
            rep.append(f"    {r.skill.replace('skill_', ''):<28} {r.pct:>5.1f}%  (CI95 {r.ci_low_pct:.1f}-{r.ci_high_pct:.1f}%, n={r.n})")
        if sub.empty:
            rep.append("    (không có skill nào đạt ngưỡng core)")

    rep.append("\n=== Độ rộng kỹ năng theo vị trí x nguồn (KHÔNG so sánh chéo vị trí khi khác cơ cấu nguồn) ===")
    piv = breadth[breadth.source != "TẤT CẢ NGUỒN (tham khảo, không dùng để so sánh vị trí)"]
    rep.append(piv.pivot(index="role", columns="source", values="n_hard_skills_mean").to_string())
    small_cells = piv[~piv.cell_reliable]
    if len(small_cells):
        rep.append(f"\n  Cảnh báo: {len(small_cells)} ô (vị trí x nguồn) có n < {MIN_N_BREADTH} job, số trung bình chỉ mang tính tham khảo, KHÔNG dùng để so sánh:")
        for r in small_cells.itertuples():
            rep.append(f"    {r.role} x {r.source}: n = {r.n_jobs}")

    rep.append("\n=== Kiểm định khác biệt độ rộng kỹ năng (chỉ so sánh trong cùng 1 nguồn) ===")
    sig = tests[(tests.role_a != "-") & (tests["significant_0.05"])]
    rep.append(f"Số cặp vị trí x nguồn có đủ cỡ mẫu để kiểm định: {len(tests[tests.role_a != '-'])}")
    rep.append(f"Số cặp có khác biệt có ý nghĩa thống kê (p<0.05): {len(sig)}")
    for r in sig.itertuples():
        rep.append(f"  [{r.source}] {r.role_a} (n={r.n_a}) vs {r.role_b} (n={r.n_b}): p={r.p_value}")

    (out / "rq1_report.txt").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    print(f"\n-> {out}/rq1_core_stack.csv, rq1_breadth.csv, rq1_breadth_test.csv, rq1_report.txt")


if __name__ == "__main__":
    main()
