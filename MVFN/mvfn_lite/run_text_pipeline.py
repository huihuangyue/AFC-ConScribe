#!/usr/bin/env python3
"""
运行“候选生成 + 文本证据提取”两步小流水线。

输入：采集目录路径（包含 dom_summary.json / ax.json）。
输出：
  - <dir>/AFC/candidates.json
  - <dir>/AFC/evidence_text.json
"""

from __future__ import annotations

import argparse
import os

from .candidate_generation import generate_candidates
from .evidence_text import build_text_evidence


def main() -> int:
    p = argparse.ArgumentParser(description="MVFN-lite | 生成 candidates.json 与 evidence_text.json")
    p.add_argument("--dir", required=True, help="采集目录 data/<domain>/<ts>")
    args = p.parse_args()

    d = os.path.abspath(args.dir)
    # 清空 AFC 目录后重建
    afc = os.path.join(d, "AFC")
    try:
        if os.path.exists(afc):
            import shutil
            shutil.rmtree(afc, ignore_errors=True)
        os.makedirs(afc, exist_ok=True)
        print("[INFO] Cleaned AFC dir:", afc)
    except Exception as e:
        print("[WARN] AFC cleanup failed:", e)
    cand_path = generate_candidates(d)
    evid_path = build_text_evidence(d)
    print("[OK] candidates:", cand_path)
    print("[OK] evidence_text:", evid_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
