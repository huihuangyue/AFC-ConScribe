"""
AFCdatabaseRepair

预留目录：用于存放基于 AFC 数据库的“技能修复 / 重建”逻辑。

建议后续在此目录下拆分模块：
- locator_repair.py：利用 AfcControl / SkillCase 进行节点重定位与 selector 修复；
- program_patch.py：结合执行日志与 LLM，对 program/prepare 做小范围补丁；
- evolve_hooks.py：在修复完成后，调用 AFCdatabaseBuild.evolve 写回 SkillCase 与 theta 权重。

当前文件仅用于标记该目录为一个 Python 包，便于后续 import。
"""

