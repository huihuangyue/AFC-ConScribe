"""
LLM Prompt 模板（AFC 页面快照相关）。

注意：这里只负责构造 prompt 文本，不直接发起 LLM 调用。
真正的调用在 afc_page_snapshot._build_afc_control 中完成。
"""

from __future__ import annotations

from typing import Any, Dict

from pathlib import Path


_REFINE_TEXT_TEMPLATE_CACHE: str | None = None


def _load_refine_text_template() -> str:
    """
    从 AFCdatabaseBuild/prompt/LLM_refine_text.md 中加载 refine_text 的提示模板。

    约定该文件中存在：
      <!-- REFINE_TEXT_PROMPT_BEGIN -->
      ...（模板正文，包含 {{CONTEXT_JSON}} 占位符）...
      <!-- REFINE_TEXT_PROMPT_END -->
    """
    global _REFINE_TEXT_TEMPLATE_CACHE
    if _REFINE_TEXT_TEMPLATE_CACHE is not None:
        return _REFINE_TEXT_TEMPLATE_CACHE

    here = Path(__file__).resolve().parent
    # 提示词模板固定放在 AFCdatabaseBuild/prompt/LLM_refine_text.md
    md_path = here / "prompt" / "LLM_refine_text.md"
    if not md_path.is_file():
        _REFINE_TEXT_TEMPLATE_CACHE = ""
        return _REFINE_TEXT_TEMPLATE_CACHE

    text = md_path.read_text(encoding="utf-8")
    start_marker = "<!-- REFINE_TEXT_PROMPT_BEGIN -->"
    end_marker = "<!-- REFINE_TEXT_PROMPT_END -->"
    i1 = text.find(start_marker)
    i2 = text.find(end_marker)
    if i1 == -1 or i2 == -1 or i2 <= i1:
        _REFINE_TEXT_TEMPLATE_CACHE = ""
        return _REFINE_TEXT_TEMPLATE_CACHE
    body = text[i1 + len(start_marker) : i2]
    _REFINE_TEXT_TEMPLATE_CACHE = body.strip()
    return _REFINE_TEXT_TEMPLATE_CACHE


def build_refine_text_prompt(raw: Any, afc: Dict[str, Any]) -> str:
    """
    构造用于“文本清洗 + 规范化标签”的 LLM prompt。

    目标：
    - 根据 raw.dom_texts / raw_text / roles 等，识别功能相关文案 vs 动态文案；
    - 输出 clean_text token 列表与规范化功能标签 norm_label；
    - 可选输出 logical_name / semantic_text。
    """
    sig = afc.get("semantic_signature") or {}
    raw_text = sig.get("raw_text") or ""
    clean_text = sig.get("clean_text") or []
    roles = sig.get("role") or []
    url_path = sig.get("url_path") or ""
    context = {
        "control_id": raw.control_id,
        "selector": raw.selector,
        "dom_texts": raw.dom_texts,
        "ax_texts": raw.ax_texts,
        "raw_text": raw_text,
        "clean_text_draft": clean_text,
        "roles": roles,
        "url_path": url_path,
    }

    template = _load_refine_text_template()
    if not template:
        # 若模板缺失，退回一个极简 prompt，仍保证可用
        return f"CONTEXT_JSON:\n{context}\n\nReturn a JSON object with keys: clean_text, norm_label, logical_name, semantic_text."

    return template.replace("{{CONTEXT_JSON}}", str(context))
