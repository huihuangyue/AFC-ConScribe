"""
AFCdatabaseEvolve

用于基于现有 AFC 数据库进行“进化”的模块包。

设计意图：
  - 输入：全局 AFC 数据库（如 workspace/AFCdatabase/db/abstract_skills_global.jsonl）
          以及一个或多个新的 run_dir（workspace/data/<domain>/<ts>/）；
  - 输出：更新后的 AFC 数据库，其中：
      * 新的 run_dir 被纳入对应 abstract_skill_id 的 skill_cases / afc_controls / concrete_skills；
      * 根据新 run_dir 上的执行结果与 diff 信息，更新 SkillCase 的 R_history 与 theta_weights；
      * 为每次修复/重建打上 (L_S, L_A) 与 rebuild_grade 等标签（参见 workspace/进化/评级.md）。

后续可以在本目录中添加模块，例如：
  - loader.py        负责读写 JSONL 形式的 AFC 库；
  - integrate_run.py 将单个 run_dir 的 afc_skill_snapshot 合并进全局库；
  - update_case.py   根据执行轨迹更新 SkillCase 与 theta_weights；
  - cli.py           提供命令行入口，便于批量对 run_dir 进行“进化更新”。

当前文件仅作为占位，标记该目录为 Python 包，方便后续 import 与扩展。
"""

