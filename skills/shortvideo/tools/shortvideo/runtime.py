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
_bg_rgba_cache = None
_glow_cache = {}
# 强度缩放表 / 纯色贴图，避免每帧重复建表、重复做 RGB->RGBA 转换
_glow_luts = {}
_glow_solids = {}


def _glow_mask(r, strength=255):
    """径向辉光蒙版（按半径缓存形状，不可变对象跨帧复用）。

    原实现每次都在约 370x370 的蒙版上跑一遍 Python 逐像素回调，grid() 一帧最多调 9 次；
    现在形状按半径只算一次（strength=255 时表值就是 (255-v)^2/255，口径不变），
    强度缩放在 glow() 里用一次 C 层 LUT 查表完成。
    """
    key = ("mask", r, strength)
    if key not in _glow_cache:
        s = r * 2
        m = Image.radial_gradient("L").resize((s, s))
        m = m.point([int((255 - v) ** 2 / 255 * strength / 255) for v in range(256)])
        _glow_cache[key] = m
    return _glow_cache[key]


def _strength_lut(s):
    """v -> int(v*s) 的 256 级口径表（按强度缓存，表本身极小）。"""
    lut = _glow_luts.get(s)
    if lut is None:
        lut = [int(v * s) for v in range(256)]
        if len(_glow_luts) > 512:  # 防御：强度是连续量，表别无限长
            _glow_luts.clear()
        _glow_luts[s] = lut
    return lut


def _solid_rgba(color, side):
    """纯色 RGBA 贴图缓存：paste(mask) 时 Pillow 会把 RGB 源转成 RGBA，
    每次新建等于每帧多做 N 次小图转换，这里按 (颜色, 边长) 复用。"""
    key = (tuple(color), side)
    sprite = _glow_solids.get(key)
    if sprite is None:
        sprite = Image.new("RGBA", (side, side), key[0] + (255,))
        _glow_solids[key] = sprite
    return sprite


def background():
    global _bg_cache
    if _bg_cache is not None:
        return _bg_cache.copy()
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


def background_rgba():
    """缓存的 RGBA 背景副本，等价于 background().convert("RGBA")。

    逐帧渲染时背景是只读底图，转换结果可以缓存；省掉每帧一次整帧 RGB->RGBA
    转换（1080x1920 约 8MB 的分配+写），返回的仍是一份独立副本，调用方可随意画。
    """
    global _bg_rgba_cache
    if _bg_rgba_cache is None or _bg_rgba_cache.size != (W, H):
        _bg_rgba_cache = background().convert("RGBA")
    return _bg_rgba_cache.copy()


def glow(img, cx, cy, r, color, strength=1.0):
    if strength <= 0.01 or r < 4:
        return
    lut = _strength_lut(min(1.0, strength))
    mask = _glow_mask(r).point(lut)
    img.paste(_solid_rgba(color, r * 2), (int(cx - r), int(cy - r)), mask)

# ---------------------------------------------------------------- 文本清洗（缺字兜底）

# wqy-zenhei 没有这些字形，会渲染成"豆腐块"；统一换成等价可显示字符
_FALLBACK = {
    "\u2212": "-",      # MINUS SIGN
    "\u2012": "-", "\u2013": "-", "\u2014": "--", "\u2011": "-",
    "\u2216": "\\", "\u00a0": " ",
    "\ufe0f": "", "\ufe0e": "", "\u200b": "", "\u200d": "",
    "\u26a0": "注意", "\u2705": "✓", "\u274c": "✗", "\u2714": "✓", "\u2716": "✗",
    "\u2b50": "★", "\u2728": "", "\u2764": "", "\ud83d": "",
}
_TOFU_CACHE = {}
# 两个"一定不存在"的字符，用来判定某个字形是不是 notdef 方块
_TOFU_REFS = ("\ue000", "\u4db6")


def _is_tofu(ch, bold=False):
    key = (ch, bold)
    hit = _TOFU_CACHE.get(key)
    if hit is None:
        try:
            f = font(48, bold)
            sig = bytes(f.getmask(ch))
            hit = any(sig == bytes(font(48, bold).getmask(ref)) for ref in _TOFU_REFS)
        except Exception:  # noqa: BLE001
            hit = False
        _TOFU_CACHE[key] = hit
    return hit


def sanitize(s, bold=False):
    """把字体缺字/emoji 换成可显示字符，避免画面出现豆腐块。

    只做"显示用"清洗：旁白原文（送 TTS）不动，避免影响发音。
    """
    if not s:
        return s
    out = []
    dirty = False
    for ch in s:
        rep = _FALLBACK.get(ch)
        if rep is None and (ch in ("\n", "\t") or 0x20 <= ord(ch) < 0x7f):
            out.append(ch)
            continue
        if rep is not None:
            out.append(rep)
            dirty = True
            continue
        o = ord(ch)
        if 0x1F000 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF or o in (0x2B00, 0x2B50):
            # emoji / 杂项符号：字体基本没有，直接去掉
            dirty = True
            continue
        if _is_tofu(ch, bold):
            out.append("?")
            dirty = True
            continue
        out.append(ch)
    return "".join(out) if dirty else s
