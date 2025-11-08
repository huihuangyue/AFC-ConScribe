"""
detect.context_utils
为 Playwright 构建上下文参数的工具，解耦 collect 主流程。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def make_context_args(
    pw,
    device_name: Optional[str],
    viewport_tuple: Optional[Tuple[int, int]],
    dpr: Optional[float],
    default_viewport: Dict[str, int],
    warnings: List[dict],
) -> Dict:
    """根据设备名/自定义视口/DPR 生成 BrowserContext 的参数字典。

    规则：
    - 若提供 device_name 且在 pw.devices 存在，则以其描述为基底。
    - viewport_tuple 与 dpr 显式提供时，覆盖设备描述中的对应项。
    - 若最终未设置 viewport，则回退为 default_viewport。
    - 任何异常会记录到 warnings 而不抛出。
    """
    args: Dict = {}
    try:
        if device_name:
            try:
                descriptor = pw.devices.get(device_name)
                if descriptor:
                    args.update(descriptor)
                else:
                    warnings.append({"code": "DEVICE_NOT_FOUND", "stage": "launch", "device": device_name})
            except Exception:
                warnings.append({"code": "DEVICE_ACCESS_ERROR", "stage": "launch", "device": device_name})
        if viewport_tuple:
            args["viewport"] = {"width": int(viewport_tuple[0]), "height": int(viewport_tuple[1])}
        if dpr is not None:
            args["device_scale_factor"] = float(dpr)
        if "viewport" not in args:
            args["viewport"] = default_viewport
    except Exception as e:
        warnings.append({"code": "CONTEXT_ARGS_ERROR", "stage": "launch", "error": str(e)})
        if "viewport" not in args:
            args["viewport"] = default_viewport
    return args

