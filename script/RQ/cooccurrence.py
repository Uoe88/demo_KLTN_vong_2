#!/usr/bin/env python3
"""
cooccurrence.py  -  Phân tích tổ hợp / co-occurrence kỹ năng theo nhóm nghề
(phần "tổ hợp" của câu hỏi nghiên cứu 1, dùng chung input với rq1_core_stack.py)

Với mỗi vị trí mục tiêu, trả lời: những skill nào hay xuất hiện CÙNG NHAU nhiều
hơn mức ngẫu nhiên (ví dụ AWS thường đi kèm Docker), chứ không chỉ liệt kê skill
phổ biến riêng lẻ (đã làm ở rq1_core_stack.py).

Input:  jd_skill_binary.xlsx/.csv (giống rq1_core_stack.py)
Output (trong --outdir):
    cooc_pairs.csv       mọi cặp skill đã xét, theo từng vị trí: n cùng xuất hiện,
                          % trong vị trí, lift, xác suất có điều kiện 2 chiều
                          (p_b_given_a = P(B|A), p_a_given_b = P(A|B)), cờ đáng tin
    cooc_top_pairs.csv   top N cặp theo lift cho mỗi vị trí (dùng vẽ bảng/báo cáo)
    cooc_clusters.csv    "cụm đồng xuất hiện": các skill nối với nhau thành 1 thành
                          phần liên thông (connected components) trên đồ thị mà cạnh
                          là các cặp đạt ngưỡng lift -- đây KHÔNG phải kết quả của một
                          thuật toán phân cụm (k-means, hierarchical...), chỉ là gom
                          nhóm theo quan hệ lift cao nối tiếp nhau, nên gọi là "cụm
                          đồng xuất hiện" chứ không gọi là "cụm công nghệ"/"cluster"
                          để tránh người đọc hiểu nhầm sang clustering.
    cooc_report.txt      tóm tắt + cảnh báo cỡ mẫu

Cách chạy:
    python cooccurrence.py --input jd_skill_binary.xlsx --outdir cooc_out

Vì sao giới hạn tập skill trước khi tính cặp (không tính trên toàn bộ skill có trong
binary -- con số này đổi theo từng lần chạy build_skill_taxonomy.py, ví dụ một lần
chạy gần đây có 138 hard skill, tức C(138,2) = 9.453 cặp MỖI vị trí nếu không lọc):
  - Số skill càng nhiều, số cặp tăng theo bậc hai, phần lớn các cặp chỉ xuất hiện ở
    1-2 job -> lift không ổn định (chia cho số quá nhỏ), heatmap/network graph cũng
    rối mắt.
  - Script chỉ xét các skill có tần suất >= --min-skill-pct trong vị trí đó (mặc định
    5%, tức skill đã "đủ phổ biến" theo đúng ngưỡng dùng để chọn cột trong
    build_skill_taxonomy.py), rồi trong số các cặp đó mới tiếp tục lọc theo số job
    thực sự có CẢ HAI skill (--min-pair-n, mặc định 5) trước khi tính lift.

Vì sao dùng lift thay vì chỉ đếm số job có cả hai skill:
  - Đếm thô luôn ưu tiên các skill phổ biến (SQL & Git sẽ luôn đứng đầu danh sách dù
    không có liên hệ đặc biệt gì). Lift = P(A,B) / (P(A) x P(B)) đo mức độ hai skill
    đi cùng nhau NHIỀU HƠN NGẪU NHIÊN bao nhiêu lần -- lift > 1 mới đáng chú ý.
  - Lift là một thước đo đồng xuất hiện thông thường trong thống kê, không riêng gì
    của luật kết hợp (association rule mining) -- script CHỈ mượn công thức này để
    đo độ liên kết giữa 2 skill, KHÔNG thực hiện khai phá luật kết hợp đầy đủ (không
    tính support trên toàn bộ tập, không sinh luật if-then, không có bước lọc theo
    support/confidence tối thiểu theo đúng quy trình Apriori/FP-Growth). Vì vậy cột
    p_b_given_a/p_a_given_b ở đây chỉ là xác suất có điều kiện đơn thuần, không nên
    gọi là "confidence" (thuật ngữ riêng của luật kết hợp) để tránh gây hiểu nhầm là
    có áp dụng một quy trình khai phá luật kết hợp hoàn chỉnh.
"""
import argparse
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

TARGET_ROLES = ["Backend", "Frontend", "Data Engineer", "Data Scientist", "AI Engineer"]


def role_pairs(g, skills, role, min_pair_n):
    """g: DataFrame job của 1 vị trí (đã lọc has_skills==1). Trả về DataFrame mọi cặp
    trong `skills`, kèm n/% từng skill riêng, n/% cùng xuất hiện, lift, xác suất có điều kiện."""
    n_role = len(g)
    p_single = {s: g[s].mean() for s in skills}
    rows = []
    for a, b in combinations(skills, 2):
        both = g[a] & g[b]
        n_both = int(both.sum())
        if n_role == 0 or p_single[a] == 0 or p_single[b] == 0:
            continue
        p_both = n_both / n_role
        lift = p_both / (p_single[a] * p_single[b])
        rows.append({
            "role": role, "skill_a": a, "skill_b": b,
            "n_a": int(g[a].sum()), "n_b": int(g[b].sum()), "n_both": n_both,
            "pct_both_of_role": round(100 * p_both, 1),
            "p_b_given_a": round(100 * n_both / g[a].sum(), 1) if g[a].sum() else np.nan,  # P(B|A)
            "p_a_given_b": round(100 * n_both / g[b].sum(), 1) if g[b].sum() else np.nan,  # P(A|B)
            "lift": round(lift, 2),
            "reliable": n_both >= min_pair_n,
        })
    return pd.DataFrame(rows)


def find_clusters(pairs, lift_threshold, min_n):
    """Tìm các cụm skill hay đi cùng nhau: nối 2 skill nếu lift >= ngưỡng VÀ đủ cỡ mẫu,
    rồi lấy các thành phần liên thông (connected components) -- không cần networkx,
    tự implement union-find cho gọn (không thêm dependency).

    LƯU Ý PHƯƠNG PHÁP (single-linkage): hai skill được coi là cùng cụm nếu có một
    CHUỖI các cặp liên tiếp đều đạt ngưỡng lift, kể cả khi A và cuối chuỗi không hề
    đi cùng nhau trực tiếp. Với threshold thấp (<2.0), gần như mọi skill phổ biến
    trong 1 vị trí sẽ bị nối thành 1 cụm khổng lồ (vì mỗi skill đều có ít nhất 1 cặp
    lift vừa phải) -- không còn ý nghĩa gom nhóm. Threshold >= 2.5 thường cho cụm
    tách bạch, dễ diễn giải hơn; nên thử vài giá trị --lift-threshold rồi so sánh
    cooc_clusters.csv trước khi chốt số liệu đưa vào báo cáo."""
    strong = pairs[(pairs.lift >= lift_threshold) & (pairs.n_both >= min_n)]
    nodes = sorted(set(strong.skill_a) | set(strong.skill_b))
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[max(rx, ry)] = min(rx, ry)

    for r in strong.itertuples():
        union(r.skill_a, r.skill_b)
    clusters = {}
    for n in nodes:
        clusters.setdefault(find(n), []).append(n)
    out = []
    for cid, (root, members) in enumerate(sorted(clusters.items()), start=1):
        if len(members) < 2:
            continue
        out.append({"cluster_id": cid, "n_skills": len(members),
                    "skills": ", ".join(m.replace("skill_", "") for m in sorted(members))})
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="jd_skill_binary.xlsx")
    ap.add_argument("--outdir", default="cooc_out")
    ap.add_argument("--roles", default=",".join(TARGET_ROLES))
    ap.add_argument("--min-skill-pct", type=float, default=5.0, help="chỉ xét skill có tần suất >= %% này trong vị trí (giữ ma trận gọn, tránh skill quá hiếm)")
    ap.add_argument("--min-pair-n", type=int, default=5, help="cặp skill cần có ít nhất bấy nhiêu job cùng yêu cầu cả hai mới tính là 'đáng tin' (reliable)")
    ap.add_argument("--lift-threshold", type=float, default=2.5, help="ngưỡng lift để coi 2 skill là 'hay đi cùng nhau' khi gom cụm (threshold thấp dễ nối mọi skill thành 1 cụm duy nhất qua các cặp trung gian -- xem ghi chú trong docstring)")
    ap.add_argument("--top-n", type=int, default=15, help="số cặp hiển thị trong report/cooc_top_pairs.csv cho mỗi vị trí")
    a = ap.parse_args()
    roles = [r.strip() for r in a.roles.split(",") if r.strip()]
    out = Path(a.outdir); out.mkdir(parents=True, exist_ok=True)

    df = pd.read_excel(a.input) if a.input.endswith((".xlsx", ".xls")) else pd.read_csv(a.input)
    LANGUAGE_SKILL_COLS = {"skill_english", "skill_japanese", "skill_french"}   # từ bản build_skill_taxonomy.py mới,
    # ngoại ngữ nằm chung tiền tố skill_* với kỹ năng chuyên môn -- loại ra khỏi phân tích "core stack"/"tổ hợp
    # kỹ năng" của câu hỏi này (ngoại ngữ đã được trả lời riêng ở rq2_density.py, mục "Soft skill: ...")
    skill_cols = [c for c in df.columns if c.startswith("skill_") and c not in LANGUAGE_SKILL_COLS]
    for c in skill_cols:
        df[c] = df[c].astype(bool)
    missing = [r for r in roles if r not in df.expertise_category.unique()]
    if missing:
        raise SystemExit(f"Không tìm thấy vị trí trong expertise_category: {missing}")

    all_pairs, all_top, all_clusters, rep = [], [], [], []
    rep.append(f"Vị trí: {', '.join(roles)}")
    rep.append(f"Chỉ xét skill có tần suất >= {a.min_skill_pct:.0f}% trong vị trí; cặp cần >= {a.min_pair_n} job mới 'reliable'")
    rep.append(f"Ngưỡng lift để gom cụm: >= {a.lift_threshold}\n")

    for role in roles:
        g = df[(df.expertise_category == role) & (df.has_skills == 1)].reset_index(drop=True)
        n_role = len(g)
        freq = g[skill_cols].mean() * 100
        skills = freq[freq >= a.min_skill_pct].index.tolist()
        rep.append(f"=== {role} (n={n_role} job, {len(skills)}/{len(skill_cols)} skill đạt ngưỡng tần suất) ===")
        if len(skills) < 2:
            rep.append("  Không đủ skill đạt ngưỡng để tính co-occurrence.\n")
            continue

        pairs = role_pairs(g, skills, role, a.min_pair_n)
        all_pairs.append(pairs)

        top = pairs[pairs.reliable].sort_values("lift", ascending=False).head(a.top_n)
        all_top.append(top)
        n_unreliable = int((~pairs.reliable).sum())
        rep.append(f"  Số cặp xét: {len(pairs)}  |  không đủ job (< {a.min_pair_n}) để tin cậy: {n_unreliable}")
        rep.append(f"  Top {min(a.top_n, len(top))} cặp theo lift (chỉ lấy cặp reliable):")
        for r in top.itertuples():
            rep.append(f"    {r.skill_a.replace('skill_',''):<22} + {r.skill_b.replace('skill_',''):<22} "
                       f"lift={r.lift:>5.2f}  n_both={r.n_both:>3} ({r.pct_both_of_role:>4.1f}% job)  "
                       f"P(B|A)={r.p_b_given_a:.0f}% P(A|B)={r.p_a_given_b:.0f}%")

        clusters = find_clusters(pairs, a.lift_threshold, a.min_pair_n)
        clusters.insert(0, "role", role)
        all_clusters.append(clusters)
        rep.append(f"  Cụm đồng xuất hiện (thành phần liên thông trên các cặp lift cao, "
                   f"KHÔNG phải kết quả thuật toán phân cụm) ({len(clusters)} cụm, lift>={a.lift_threshold}, n>={a.min_pair_n}):")
        for r in clusters.itertuples():
            rep.append(f"    Cụm {r.cluster_id} ({r.n_skills} skill): {r.skills}")
        rep.append("")

    # Nếu KHÔNG vị trí nào đủ skill đạt ngưỡng (--min-skill-pct quá cao, hoặc input quá ít dữ liệu),
    # các list này rỗng -- pd.concat([]) sẽ raise ValueError, nên phải kiểm tra trước thay vì gọi thẳng.
    PAIRS_COLS = ["role", "skill_a", "skill_b", "n_a", "n_b", "n_both", "pct_both_of_role",
                 "p_b_given_a", "p_a_given_b", "lift", "reliable"]
    CLUSTER_COLS = ["role", "cluster_id", "n_skills", "skills"]
    if all_pairs:
        pd.concat(all_pairs, ignore_index=True).to_csv(out / "cooc_pairs.csv", index=False, encoding="utf-8-sig")
        pd.concat(all_top, ignore_index=True).to_csv(out / "cooc_top_pairs.csv", index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=PAIRS_COLS).to_csv(out / "cooc_pairs.csv", index=False, encoding="utf-8-sig")
        pd.DataFrame(columns=PAIRS_COLS).to_csv(out / "cooc_top_pairs.csv", index=False, encoding="utf-8-sig")
        rep.append("\n[!] KHÔNG có vị trí nào đủ skill đạt ngưỡng --min-skill-pct -- cooc_pairs.csv/"
                   "cooc_top_pairs.csv rỗng. Thử giảm --min-skill-pct hoặc kiểm tra lại input.")
    if all_clusters:
        pd.concat(all_clusters, ignore_index=True).to_csv(out / "cooc_clusters.csv", index=False, encoding="utf-8-sig")
    else:
        pd.DataFrame(columns=CLUSTER_COLS).to_csv(out / "cooc_clusters.csv", index=False, encoding="utf-8-sig")
    if roles and all_pairs:
        rep.append("Lưu ý phương pháp: cụm được gom bằng single-linkage (xem find_clusters() trong "
                   "cooccurrence.py) -- nếu một vị trí ra đúng 1 cụm chiếm gần hết số skill, thử tăng "
                   "--lift-threshold (vd 2.5-3.0) để có các cụm tách bạch, dễ diễn giải hơn thay vì 1 khối lớn.")
    (out / "cooc_report.txt").write_text("\n".join(rep), encoding="utf-8")
    print("\n".join(rep))
    print(f"\n-> {out}/cooc_pairs.csv, cooc_top_pairs.csv, cooc_clusters.csv, cooc_report.txt")


if __name__ == "__main__":
    main()
