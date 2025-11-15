"""
detect.constants
常量定义。
"""

DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
DETECT_SPEC_VERSION = "v0.1"

# 产物文件名映射（供 return_info/文档使用）
ARTIFACTS = {
    "screenshot_initial": "screenshot_initial.png",
    "screenshot_loaded": "screenshot_loaded.png",
    "screenshot_loaded_cropped": "screenshot_loaded_cropped.png",
    "screenshot_loaded_cropped_overlay": "screenshot_loaded_cropped_overlay.png",
    "screenshot_loaded_overlay": "screenshot_loaded_overlay.png",
    "screenshot_scrolled_tail": "screenshot_scrolled_tail.png",
    "screenshot_scrolled_tail_overlay": "screenshot_scrolled_tail_overlay.png",
    "dom_html": "dom.html",
    "dom_summary": "dom_summary.json",
    "dom_summary_scrolled": "dom_summary_scrolled.json",
    "dom_scrolled_new": "dom_scrolled_new.json",
    "ax": "ax.json",
    "timings": "timings.json",
    "meta": "meta.json",
    "scroll_info": "scroll_info.json",
    "controls_tree": "controls_tree.json",
    # 额外派生产物
    "roots_list": "roots.json",
    "reveal_log": "reveal_log.json",
    # DOM 片段（每个节点/控件一段 HTML）
    "tips_dir": "tips",
    "tips_index": "tips/index.json",
    # 片段导出目录与索引（按第一层控件，从上到下）
    "snippets_dir": "snippets",
    "snippets_index": "snippets/index.json",
    # 片段截图目录与索引
    "segments_dir": "segments",
    "segments_meta": "segments/index.json",
    # 主控件块与交互图
    "blocks": "blocks.json",
    "graphs_dir": "graphs",
}
