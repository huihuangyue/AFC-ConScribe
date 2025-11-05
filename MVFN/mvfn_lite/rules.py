"""规则归一化分类。

作用：使用别名词典与正则，将候选归一化为标准化标签（如 Clickable_Submit）与操作类型（Click/Input/...）。
输入：候选的证据集合（文本/图标/角色）。
输出：label, action
依赖：内置 alias 词典；可扩展。
"""

import re
from typing import Tuple
from .schema import Candidate


ALIASES = {
    "submit": {"submit", "commit", "send", "go", "继续", "提交"},
    "search": {"search", "find", "magnifier", "🔍", "搜索"},
    "login": {"login", "sign in", "log in", "登录"},
    "signup": {"sign up", "register", "创建账户", "注册"},
    "cancel": {"cancel", "abort", "取消"},
}


def classify_label_and_action(c: Candidate) -> Tuple[str, str]:
    text_blob = " ".join([e.value for e in c.evidence])
    text_blob = text_blob.lower()

    # role 优先决定操作类型
    role = (c.role or "").lower()
    if role in {"textbox", "combobox"}:
        action = "Input"
    elif role in {"button", "link", "checkbox", "radio"}:
        action = "Click"
    else:
        action = "Click"

    # 文本/图标别名匹配决定标签
    label = "Generic_Control"
    for key, vocab in ALIASES.items():
        for v in vocab:
            if re.search(re.escape(v.lower()), text_blob):
                if key == "submit":
                    label = "Clickable_Submit"
                elif key == "search":
                    label = "Clickable_Search"
                elif key == "login":
                    label = "Clickable_Login"
                elif key == "signup":
                    label = "Clickable_Signup"
                elif key == "cancel":
                    label = "Clickable_Cancel"
                break
        if label != "Generic_Control":
            break

    return label, action

