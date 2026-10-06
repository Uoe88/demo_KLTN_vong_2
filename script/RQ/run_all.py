#!/usr/bin/env python3
"""Chạy rq1..rq4 (+ rq3_pooled) và ghi kết quả vào results/ cho dashboard đọc.

Cách chạy (chỉ còn MỘT file đầu vào, do build_skill_taxonomy.py tạo ra):
    python run_all.py --input clean_out/jd_skill_binary.csv --taxonomy clean_out/skill_taxonomy.csv
Đặt run_all.py cùng thư mục với rq1..rq4, rq3_pooled.py và jd_loader.py.
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

for stream in (sys.stdout, sys.stderr):  # tránh lỗi mã hoá khi console Windows không phải UTF-8
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

REQUIRED = ["job_uid", "source", "expertise_category", "level_group", "exp_min", "city",
            "has_skills", "n_hard_skills", "skills_status"]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="jd_skill_binary.csv", help="file do build_skill_taxonomy.py tạo ra")
    ap.add_argument("--taxonomy", default="skill_taxonomy.csv")
    ap.add_argument("--scripts-dir", default=str(Path(__file__).parent))
    ap.add_argument("--results", default="results")
    a = ap.parse_args()

    for label, p in (("input", a.input), ("taxonomy", a.taxonomy)):
        if not Path(p).exists():
            sys.exit(f"Không thấy file {label}: {p} (đường dẫn tính từ thư mục đang đứng, hãy kiểm tra lại).")

    cols = pd.read_csv(a.input, nrows=0).columns
    miss = [c for c in REQUIRED if c not in cols]
    if miss or not any(c.startswith("skill_") for c in cols):
        sys.exit(f"{a.input} thiếu cột {miss} hoặc không có cột skill_* -- hãy chạy lại build_skill_taxonomy.py bản mới "
                 f"(file này phải là jd_skill_binary.csv, không phải job_skill_binary.csv).")

    sd, res = Path(a.scripts_dir), Path(a.results)
    jobs = [("rq1_core_stack.py", "rq1_out", []), ("rq2_density.py", "rq2_out", ["--taxonomy", a.taxonomy]),
            ("rq3_experience.py", "rq3_out", []), ("rq3_pooled.py", "rq3_out", []), ("rq4_geography.py", "rq4_out", [])]
    for script, _, _ in jobs:
        if not (sd / script).exists():
            sys.exit(f"Không thấy {sd / script} -- đặt run_all.py cùng thư mục với các script rq*.")

    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}  # script con in tiếng Việt qua pipe
    for script, out, extra in jobs:
        cmd = [sys.executable, str(sd / script), "--input", a.input, "--outdir", str(res / out), *extra]
        print(">>", " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
        if r.returncode:
            sys.exit(f"{script} lỗi:\n{(r.stderr or r.stdout)[-1800:]}")
    print("Xong. Mở dashboard: streamlit run app.py")


if __name__ == "__main__":   # import run_all (vd từ app.py/analytics.py) sẽ KHÔNG tự chạy lại rq1..rq4
    main()