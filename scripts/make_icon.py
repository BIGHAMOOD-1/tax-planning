"""生成应用图标 assets/app.ico（Update 6.2）。

蓝紫渐变 + 白色「税」字，多尺寸 ICO（16/32/48/64/128/256）。
用法：python scripts/make_icon.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "assets"
SIZES = [16, 32, 48, 64, 128, 256]
C1 = (47, 107, 255)    # #2f6bff
C2 = (109, 94, 252)    # #6d5efc
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]


def _lerp(a, b, t):
    """RGB 线性插值：t=0 取 a，t=1 取 b，用于渐变。"""
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _base(size: int = 256) -> Image.Image:
    """生成底图：对角渐变色 + 圆角透明遮罩。"""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 对角渐变
    for y in range(size):
        d.line([(0, y), (size, y)], fill=_lerp(C1, C2, y / (size - 1)))
    # 圆角遮罩
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=int(size * 0.22), fill=255)
    img.putalpha(mask)
    return img


def _draw_glyph(img: Image.Image) -> None:
    """在底图中央绘制白色「税」字；无中文字体时退化为圆环。"""
    size = img.width
    d = ImageDraw.Draw(img)
    font = None
    # 依次尝试候选字体，取第一个可用的中文字体
    for fp in FONT_CANDIDATES:
        if Path(fp).exists():
            try:
                font = ImageFont.truetype(fp, int(size * 0.60))
                break
            except Exception:  # noqa: BLE001
                continue
    if font is None:
        # 退化：画一个白色圆点环（无中文字体时）
        r = int(size * 0.26)
        cx = cy = size // 2
        d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 255, 255, 235), width=max(2, size // 16))
        return
    text = "税"
    box = d.textbbox((0, 0), text, font=font)
    # 用 bbox 计算视觉居中位置（补偿字体基线偏移）
    w, h = box[2] - box[0], box[3] - box[1]
    d.text(((size - w) / 2 - box[0], (size - h) / 2 - box[1]), text, font=font, fill=(255, 255, 255, 245))


def main():
    """生成多尺寸 ICO 与 PNG 图标到 assets/。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = _base(256)
    _draw_glyph(base)
    ico = OUT_DIR / "app.ico"
    # 一次保存多尺寸 ICO（Windows 各场景按需选用）
    base.save(ico, format="ICO", sizes=[(s, s) for s in SIZES])
    base.save(OUT_DIR / "app.png")
    print(f"[OK] 已生成 {ico} 与 {OUT_DIR / 'app.png'}")


if __name__ == "__main__":
    main()
