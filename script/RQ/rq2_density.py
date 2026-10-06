#!/usr/bin/env python3
"""
rq2_density.py  -  Câu hỏi nghiên cứu 2
"Mật độ xuất hiện của các yêu cầu về Cloud/DevOps, AI/LLM và Soft skill (Tiếng Anh,
 Tiếng Pháp, Tiếng Nhật) trong các bài đăng tuyển dụng biến thiên như thế nào giữa
 các nhóm nghề?"

Input:
    jd_skill_binary.xlsx/.csv  (file chính -- cột ngoại ngữ là skill_english/skill_japanese/skill_french)
    skill_taxonomy.csv         (để biết skill_* nào thuộc nhóm Cloud / DevOps & Infra /
                                 AI/ML -- tự động map, không liệt kê tay trong code này)
Output (trong --outdir):
    rq2_density.csv     nhóm nghề x nhóm công nghệ (Cloud/DevOps, AI/LLM, mỗi ngoại ngữ,
                         soft_skill gộp): %% job yêu cầu, CI 95%%, cờ đáng tin
    rq2_report.txt      bảng tóm tắt dễ đọc + cảnh báo cỡ mẫu

Cách chạy:
    python rq2_density.py --input jd_skill_binary.xlsx --taxonomy skill_taxonomy.csv --outdir rq2_out

Khác với rq1_core_stack.py: câu hỏi 2 không giới hạn ở 5 vị trí cốt lõi mà hỏi về
"các nhóm nghề" nói chung, nên mặc định script chạy trên TẤT CẢ expertise_category
có trong dữ liệu (14 nhóm), không chỉ 5 nhóm của câu hỏi 1. Dùng --roles để giới hạn
nếu muốn so sánh riêng một tập vị trí.

Định nghĩa "mật độ": tỷ lệ %% JOB trong 1 nhóm nghề có YÊU CẦU ÍT NHẤT 1 skill thuộc
nhóm công nghệ đó (không phải trung bình số skill) -- đây là cách đọc tự nhiên của
"mật độ xuất hiện của yêu cầu" trong câu hỏi nghiên cứu. Số skill trung bình trong
mỗi nhóm công nghệ (độ sâu, không chỉ có/không) được tính thêm ở cột *_avg_n_skills
để tham khảo thêm nếu cần.
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_TECH_GROUPS = {
    "Cloud/DevOps": ["Cloud", "DevOps & Infra"],
    "AI/LLM": ["AI/ML"],
}
LANGUAGES = ["english", "japanese", "french"]   # khớp cột skill_english/skill_japanese/skill_french


def wilson_ci(k, n, z=1.96):
    if n == 0:
        return np.nan, np.nan
    p = k / n
    denom = 1 + z**2 / n
    center = (p + z**2 / (2 * n)) / denom
    half = (z * np.sqrt(p * (1 - p) / n + z**2 / (4 * n**2))) / denom
    return max(0.0, center - half), min(1.0, center + half)


def build_tech_group_cols(skill_cols, taxonomy_path, group_spec):
    """Map skill_* -> nhóm công nghệ (Cloud/DevOps, AI/LLM...) bằng skill_taxonomy.csv,
    dùng đúng công thức tạo slug như trong build_skill_taxonomy.py để khớp tên cột."""
    import re
    tax = pd.read_csv(taxonomy_path)
    tax = tax[tax.type == "hard"].copy()

    def slug(canon):
        s = canon.lower().replace("c++", "cpp").replace("c#", "csharp").replace(".net", "dotnet")
        return "skill_" + re.sub(r"[^a-z0-9]+", "_", s).strip("_")

    tax["col"] = tax.canonical.map(slug)
    col_to_group = dict(zip(tax.col, tax.group))
    out = {}
    for label, taxo_groups in group_spec.items():
        cols = [c for c in skill_cols if col_to_group.get(c) in taxo_groups]
        missing = [c for c in skill_cols if c not in col_to_group]  # chẩn đoán, không phải lỗi của nhóm này
        out[label] = cols
    return out, col_to_group


def density_table(df, roles, groups, min_n):
    rows = []
    for role in roles:
        g = df[(df.expertise_category == role) & (df.has_skills == 1)]
        n = len(g)
        reliable = n >= min_n
        for label, cols in groups.items():
            if not cols:
                continue
            has_any = g[cols].any(axis=1)
            k = int(has_any.sum())
            pct = 100 * k / n if n else np.nan
            lo, hi = wilson_ci(k, n)
            avg_n = round(g[cols].sum(axis=1).mean(), 1) if n else np.nan
            rows.append({"role": role, "n_jobs_with_skills": n, "tech_group": label, "n_skill_cols_in_group": len(cols),
                         "n_jobs_matched": k, "pct": round(pct, 1) if n else np.nan,
                         "ci_low_pct": round(100 * lo, 1) if n else np.nan,
                         "ci_high_pct": round(100 * hi, 1) if n else np.nan,
                         "avg_n_skills_in_group": avg_n, "role_sample_reliable": reliable})
    return pd.DataFrame(rows)


def language_table(df, roles, min_n):
    """Từ bản build_skill_taxonomy.py mới, ngoại ngữ là các cột skill_english/skill_japanese/
    skill_french (không còn cột soft_skill/soft_english gộp sẵn như bản cũ) -- tự tính cột
    'bất kỳ ngoại ngữ' bằng OR ba cột này thay vì đọc 1 cột có sẵn."""
    lang_cols = [f"skill_{l}" for l in LANGUAGES]
    missing = [c for c in lang_cols if c not in df.columns]
    if missing:
        raise SystemExit(f"Thiếu cột ngoại ngữ trong input: {missing} -- kiểm tra lại input có phải "
                         f"xuất từ build_skill_taxonomy.py bản mới không (ngôn ngữ giờ là skill_english/"
                         f"skill_japanese/skill_french, không còn soft_english...).")
    df = df.copy()
    df["_any_lang"] = df[lang_cols].any(axis=1)
    rows = []
    for role in roles:
        g = df[df.expertise_category == role]            # ngoại ngữ lấy từ JD text, không phụ thuộc has_skills
        n = len(g)
        reliable = n >= min_n
        cols = {"_any_lang": "Bất kỳ ngoại ngữ (gộp)", **{f"skill_{l}": l.capitalize() for l in LANGUAGES}}
        for col, label in cols.items():
            k = int(g[col].sum())
            pct = 100 * k / n if n else np.nan
            lo, hi = wilson_ci(k, n)
            rows.append({"role": role, "n_jobs": n, "tech_group": f"Soft skill: {label}",
                         "n_skill_cols_in_group": 1, "n_jobs_matched": k,
                         "pct": round(pct, 1) if n else np.nan,
                         "ci_low_pct": round(100 * lo, 1) if n else np.nan,
                         "ci_high_pct": round(100 * hi, 1) if n else np.nan,
                         "avg_n_skills_in_group": np.nan, "role_sample_reliable": reliable})
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="jd_skill_binary.xlsx")
    ap.add_argument("--taxonomy", default="skill_taxonomy.csv")
    ap.add_argument("--outdir", default="rq2_out")
    ap.add_argument("--roles", default="ALL", help="'ALL' (mặc định, dùng mọi expertise_category) hoặc danh sách cách nhau bằng dấu phẩy")
    ap.add_argument("--min-n", type=int, default=50, help="dưới ngưỡng này, %% của nhóm nghề được coi là KHÔNG đủ tin cậy")
    a = ap.parse_args()
    out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(a.input) if a.input.endswith((".xlsx", ".xls")) else pd.read_csv(a.input)
    skill_cols = [c for c in df.columns if c.startswith("skill_")]
    roles = sorted(df.expertise_category.dropna().unique(), key=lambda r: -(df.expertise_category == r).sum()) \
        if a.roles == "ALL" else [r.strip() for r in a.roles.split(",") if r.strip()]
    missing = [r for r in roles if r not in df.expertise_category.unique()]
    if missing:
        raise SystemExit(f"Không tìm thấy vị trí trong expertise_category: {missing}")

    groups, col_to_group = build_tech_group_cols(skill_cols, a.taxonomy, DEFAULT_TECH_GROUPS)
    # 2 loại cột KHÔNG nằm trong skill_taxonomy.csv (type='hard') một cách CÓ CHỦ ĐÍCH, không phải lỗi:
    #   - skill_english/japanese/french: taxonomy xếp type='soft' (ngoại ngữ), build_tech_group_cols chỉ
    #     đọc type='hard' nên không thấy -- nhưng language_table() đã xử lý riêng các cột này rồi.
    #   - skill_cert_security: cột GỘP tạo ra lúc build binary (CISSP/CEH/OSCP/CISM -> 1 cột), bản thân
    #     nó không phải 1 canonical trong taxonomy nên không có group riêng -- không thuộc Cloud/DevOps/AI.
    KNOWN_NON_TAXONOMY_COLS = {f"skill_{l}" for l in LANGUAGES} | {"skill_cert_security"}
    unmapped = [c for c in skill_cols if c not in col_to_group and c not in KNOWN_NON_TAXONOMY_COLS]

    tech = density_table(df, roles, groups, a.min_n)
    lang = language_table(df, roles, a.min_n)
    all_ = pd.concat([tech, lang], ignore_index=True)
    all_.to_csv(out / "rq2_density.csv", index=False, encoding="utf-8-sig")

    rep = [f"Vị trí: {'TẤT CẢ (' + str(len(roles)) + ' nhóm)' if a.roles == 'ALL' else ', '.join(roles)}",
           f"Ngưỡng cỡ mẫu tin cậy: n >= {a.min_n} job",
           f"Nhóm Cloud/DevOps = {len(groups.get('Cloud/DevOps', []))} skill, AI/LLM = {len(groups.get('AI/LLM', []))} skill "
           f"(map tự động từ {a.taxonomy}, cột group = 'Cloud'/'DevOps & Infra' và 'AI/ML')",
           ""]
    if unmapped:
        rep.append(f"[!] {len(unmapped)} cột skill_* trong {a.input} không khớp được với {a.taxonomy} -- CẦN KIỂM TRA "
                   f"(có thể taxonomy khác phiên bản với binary, hoặc có cột mới chưa biết): "
                   + ", ".join(unmapped[:15]) + (" ..." if len(unmapped) > 15 else ""))
        rep.append("")

    rep.append("=== Mật độ Cloud/DevOps và AI/LLM theo nhóm nghề (%% job, sắp theo n job giảm dần) ===")
    piv = tech.pivot(index="role", columns="tech_group", values="pct")
    nj = tech.drop_duplicates("role").set_index("role")["n_jobs_with_skills"]
    piv.insert(0, "n_jobs", nj)
    piv = piv.sort_values("n_jobs", ascending=False)
    rep.append(piv.to_string())
    small = tech[~tech.role_sample_reliable].role.unique()
    if len(small):
        rep.append(f"\n  Cảnh báo cỡ mẫu nhỏ (n < {a.min_n}): " + ", ".join(small))

    rep.append("\n=== Mật độ ngoại ngữ / soft skill theo nhóm nghề (%% job, từ JD text) ===")
    pivl = lang.pivot(index="role", columns="tech_group", values="pct")
    nj2 = lang.drop_duplicates("role").set_index("role")["n_jobs"]
    pivl.insert(0, "n_jobs", nj2)
    pivl = pivl.sort_values("n_jobs", ascending=False)
    rep.append(pivl.to_string())

    rep.append("\n=== Khoảng tin cậy 95%% (Wilson) cho 3 chỉ số chính, từng vị trí ===")
    for role in roles:
        rows = all_[all_.role == role]
        parts = []
        for label in ["Cloud/DevOps", "AI/LLM", "Soft skill: Bất kỳ ngoại ngữ (gộp)"]:
            r = rows[rows.tech_group == label]
            if len(r):
                r = r.iloc[0]
                parts.append(f"{label}={r.pct:.1f}% (CI {r.ci_low_pct:.1f}-{r.ci_high_pct:.1f}, n={r.n_jobs_matched})")
        rep.append(f"  {role:<18} " + "  |  ".join(parts))

    (out / "rq2_report.txt").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    print(f"\n-> {out}/rq2_density.csv, rq2_report.txt")


if __name__ == "__main__":
    main()
