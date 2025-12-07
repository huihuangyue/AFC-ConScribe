"""
AFC 抽象技能（per-run_dir 索引）模块包。

当前主要对外提供：
- build_skill_snapshot(run_dir): 为单个 run_dir 生成技能级 AFC 抽象快照。
"""

from .skill_snapshot import build_skill_snapshot

__all__ = ["build_skill_snapshot"]

