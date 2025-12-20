"""
init_global_afc

从单个或多个 run_dir 的 AFC 快照构建“初始版”全局 AFC 库的辅助模块。

设计意图：
  - 这是 AFCdatabaseBuild 层面的“冷启动”入口；
  - 只负责把现有的 AfcPageSnapshot / AfcSkillSnapshot 聚合成一个 JSONL 形式的全局库；
  - 不负责后续的演化更新（那部分由 AFCdatabaseEvolve 处理）。

典型用法：

  from pathlib import Path
  from AFCdatabaseBuild.init_global_afc import build_initial_global_afc

  run_dir = Path(\"workspace/data/jd_com/20251219213050\")  # 单个 run_dir
  out_db = Path(\"workspace/AFCdatabase/db/abstract_skills_global.jsonl\")

  build_initial_global_afc(
      run_dirs=[run_dir],
      out_path=out_db,
      use_llm=False,   # 或 True：是否调用 LLM_global_afc_aggregate 做初始权重估计
      overwrite=True,  # 若目标文件已存在，是否覆盖
  )

说明：
  - 本模块只是对 AFCdatabaseBuild.global_db.integrate_run_dir 的简单封装；
  - full_preprocess_flow.py 只负责生成单个 run_dir 的 AfcPageSnapshot / AfcSkillSnapshot，
    不会调用本模块；全局库的构建应由单独的脚本或离线任务调用本模块完成。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

import shutil

from .global_db import GlobalDb, JsonDict, integrate_run_dir, save_global_db


def build_initial_global_afc(
    run_dirs: Iterable[Path],
    out_path: Path,
    *,
    use_llm: bool = False,
    overwrite: bool = True,
) -> Path:
    """从一批 run_dir 构建“初始版”全局 AFC 库，并写入 out_path（JSONL）。

    参数：
      - run_dirs:
          - 一组 Detect+AFC 产物目录：workspace/data/<domain>/<ts>/；
          - 要求每个 run_dir 下已存在：
              afc/afc_page_snapshot.json
              afc/afc_skill_snapshot.json
      - out_path:
          - 目标全局库路径，通常类似：
              workspace/AFCdatabase/db/abstract_skills_global.jsonl
      - use_llm:
          - 若为 True，则在 integrate_run_dir 时尝试调用
            prompt/LLM_global_afc_aggregate.md 对初始 theta_weights 做精细估计；
          - 若为 False，则使用默认权重初始化。
      - overwrite:
          - 若为 True 且 out_path 已存在，则覆盖原文件；
          - 若为 False 且 out_path 已存在，则抛出 FileExistsError。
    """
    out_path = Path(out_path)
    if out_path.exists() and not overwrite:
        raise FileExistsError(f"global AFC db already exists: {out_path}")

    # 冷启动：从空的 GlobalDb 开始，依次吸收各个 run_dir 的 AFC 快照。
    db = GlobalDb(rows=[], index={}, path=None)

    # 为了日志/调试方便，将 run_dirs 展开为列表
    rds: List[Path] = [Path(rd) for rd in run_dirs]

    for rd in rds:
        integrate_run_dir(db, rd, use_llm=use_llm)

    # 写盘：全局 AFC 库 JSONL
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_global_db(db, out_path)

    # 将依赖的 AFC 快照一并打包到 out_path 所在目录下，方便后续 Repair / Evolve：
    #   out_path
    #   out_path.parent / "runs" / <domain>__<ts> / afc_page_snapshot.json
    #                                            / afc_skill_snapshot.json
    bundle_root = out_path.parent / "runs"
    for rd in rds:
        rd = Path(rd).resolve()
        page_src = rd / "afc" / "afc_page_snapshot.json"
        skill_src = rd / "afc" / "afc_skill_snapshot.json"
        if not page_src.is_file() or not skill_src.is_file():
            # 该 run_dir 上缺少 AFC 快照时，只跳过复制，不影响全局库本身
            continue

        # 使用 <domain>__<ts> 作为子目录名：
        #   - domain = run_dir 的父目录名（workspace/data/<domain>/<ts>/）
        #   - ts     = run_dir.name
        domain_name = rd.parent.name
        ts_name = rd.name
        tgt_dir = bundle_root / f"{domain_name}__{ts_name}"
        tgt_dir.mkdir(parents=True, exist_ok=True)

        shutil.copy2(page_src, tgt_dir / "afc_page_snapshot.json")
        shutil.copy2(skill_src, tgt_dir / "afc_skill_snapshot.json")

    return out_path


__all__ = ["build_initial_global_afc"]
