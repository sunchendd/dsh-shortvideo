"""shortvideo 运行时：题材无关的渲染/配音/合成引擎。

设计原则（对应 SKILL.md）：
  - 题材包只写 build(ctx) 返回 {镜头id: scene(img, t, dur, ctx)}
  - 绘图基元全部收进 SceneAPI，题材包不直接碰 Pillow 底层
  - 数学内核由题材包自己提供（先算对，再画对）
  - WSL/Linux 环境自适应：字体探测、ffmpeg 由 imageio-ffmpeg 提供
"""
import math
import os

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- 主题

W, H, FPS = 1080, 1920, 30

BG_TOP = (12, 17, 32)
BG_BOT = (4, 6, 14)
TILE_OFF = (18, 25, 43)
TILE_OFF_EDGE = (46, 60, 96)
TILE_ON = (255, 82, 116)
TILE_ON_EDGE = (255, 148, 170)
ACCENT = (61, 232, 255)
AMBER = (255, 181, 71)
GREEN = (54, 227, 148)
TEXT = (236, 242, 255)
DIM = (128, 146, 180)
FAINT = (60, 74, 108)

# Linux 下按优先级探测 CJK 字体
_FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # 兜底（无中文但防崩）
]
_MATH_CANDIDATES = _FONT_CANDIDATES[:]

_font_path = None
_cache = {}


def _pick(candidates):
    for p in candidates:
        if os.path.exists(p):
            return p
    raise SystemExit(
        "[x] 未找到可用字体。请安装：apt-get install -y fonts-wqy-zenhei "
        "（或 fonts-noto-cjk），或把字体路径加进 runtime._FONT_CANDIDATES"
    )


def font(size, bold=False, math=False):
    """math 体在 Linux 下没有独立数学字体，直接复用正文字体（CJK 覆盖一致）。"""
    global _font_path
    if _font_path is None:
        _font_path = _pick(_FONT_CANDIDATES)
    key = (size, bold, math)
    if key not in _cache:
        _cache[key] = ImageFont.truetype(_font_path, size)
    return _cache[key]


def font_info():
    global _font_path
    if _font_path is None:
        _font_path = _pick(_FONT_CANDIDATES)
    return _font_path


# ---------------------------------------------------------------- 缓动

def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def lerp(a, b, t):
    return a + (b - a) * t


def seg(t, start, end):
    if end <= start:
        return 1.0 if t >= end else 0.0
    return clamp((t - start) / (end - start))


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_in_out(t):
    return 4 * t ** 3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_back(t, s=1.6):
    t = clamp(t) - 1
    return 1 + (s + 1) * t ** 3 + s * t ** 2


# ---------------------------------------------------------------- 背景（一次生成，帧间复用）

_bg_cache = None
_glow_cache = {}


def _glow_mask(r, strength=255):
    key = ("mask", r, strength)
    if key not in _glow_cache:
        s = r * 2
        m = Image.radial_gradient("L").resize((s, s))
        m = m.point(lambda v: int((255 - v) ** 2 / 255 * strength / 255))
        _glow_cache[key] = m
    return _glow_cache[key]


def background():
    global _bg_cache
    if _bg_cache is not None:
        return _bg_cache.copy()
    import PIL
    grad = Image.linear_gradient("L").resize((W, H))
    top = Image.new("RGB", (W, H), BG_TOP)
    bot = Image.new("RGB", (W, H), BG_BOT)
    bg = Image.composite(bot, top, grad)
    d = ImageDraw.Draw(bg)
    for y in range(0, H, 60):
        for x in range(0, W, 60):
            d.point((x, y), fill=(26, 34, 54))
    _bg_cache = bg
    return bg.copy()


def glow(img, cx, cy, r, color, strength=1.0):
    if strength <= 0.01 or r < 4:
        return
    rgb = Image.new("RGB", (r * 2, r * 2), color)
    mask = _glow_mask(r).point(lambda v: int(v * min(1.0, strength)))
    img.paste(rgb, (int(cx - r), int(cy - r)), mask)
