"""OCR 适配：包装 pytesseract。

需要系统已安装 Tesseract 可执行程序。
"""

from typing import Optional


def ocr_text(image_path: str, bbox=None) -> Optional[str]:
    try:
        from PIL import Image  # type: ignore
        import pytesseract  # type: ignore
    except Exception:
        return None
    img = Image.open(image_path)
    if bbox:
        x, y, w, h = int(bbox.x), int(bbox.y), int(bbox.w), int(bbox.h)
        img = img.crop((x, y, x + w, y + h))
    text = pytesseract.image_to_string(img)
    text = (text or "").strip()
    return text or None

