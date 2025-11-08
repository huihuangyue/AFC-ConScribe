"""
detect.errors
中文异常类型定义。

提供 CollectError 作为采集流程的统一错误封装，
便于在失败时写入 meta.json 并可选择抛出异常。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class CollectError(Exception):
    """采集致命错误。

    code: 错误码（如 INVALID_URL/NAV_TIMEOUT 等）
    stage: 出错阶段（init/launch/navigate/...）
    message: 人类可读的错误信息
    out_dir: 可选，已写入产物的目录（便于排查）
    original: 可选，原始异常对象
    """

    code: str
    stage: str
    message: str
    out_dir: Optional[str] = None
    original: Optional[Exception] = None

    def __str__(self) -> str:  # pragma: no cover
        base = f"[{self.code}@{self.stage}] {self.message}"
        if self.out_dir:
            base += f" (out_dir={self.out_dir})"
        return base

