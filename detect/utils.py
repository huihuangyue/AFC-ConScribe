"""
detect.utils
中文通用工具函数：路径/时间/JSON/URL 校验/视口解析等。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, Optional, Tuple, Union
from urllib.parse import urlparse

# 兼容包内与脚本直接运行两种方式的导入
try:  # pragma: no cover
    from .errors import CollectError
except Exception:  # pragma: no cover
    from errors import CollectError  # type: ignore


def sanitize_domain(url: str) -> str:
    """将 URL 的域名清洗为文件系统安全的 key（如 baidu_com）。"""
    netloc = urlparse(url).netloc
    if ":" in netloc:
        netloc = netloc.split(":", 1)[0]
    if netloc.lower().startswith("www."):
        netloc = netloc[4:]
    key = re.sub(r"[^0-9A-Za-z]", "_", netloc)
    key = re.sub(r"_+", "_", key).strip("_")
    return key or "unknown"


def timestamp_yyyymmddhhmmss() -> str:
    """返回当前时间戳，格式 YYYYMMDDHHMMSS。"""
    return datetime.now().strftime("%Y%m%d%H%M%S")


def ensure_unique_dir(path: str) -> str:
    """确保目录唯一存在，如已存在则追加 -1/-2 后缀。"""
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)
        return path
    i = 1
    while True:
        alt = f"{path}-{i}"
        if not os.path.exists(alt):
            os.makedirs(alt, exist_ok=True)
            return alt
        i += 1


def write_json(path: str, obj: Dict[str, Any]) -> None:
    """以 UTF-8 与缩进写入 JSON 文件。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_json_config(path: Optional[str]) -> Dict[str, Any]:
    """加载 JSON 配置文件（若不存在或解析失败则返回空 dict）。"""
    if not path:
        return {}
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def validate_url(url: str) -> None:
    """校验 URL（仅允许 http/https），非法则抛 CollectError。"""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise CollectError(
            code="INVALID_URL",
            stage="init",
            message=f"不支持的 URL：{url}",
            out_dir=None,
        )


def parse_viewport(viewport: Union[str, Tuple[int, int], None]) -> Optional[Tuple[int, int]]:
    """解析视口参数，支持 "WxH" 或 (w,h)。失败返回 None。"""
    if viewport is None:
        return None
    if isinstance(viewport, (tuple, list)) and len(viewport) == 2:
        try:
            return int(viewport[0]), int(viewport[1])
        except Exception:
            return None
    if isinstance(viewport, str) and "x" in viewport:
        try:
            w, h = viewport.lower().split("x", 1)
            return int(w), int(h)
        except Exception:
            return None
    return None
