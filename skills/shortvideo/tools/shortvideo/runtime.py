"""shortvideo 运行时：题材无关的渲染/配音/合成引擎。

设计原则（对应 SKILL.md）：
  - 题材包只写 build(ctx) 返回 {镜头id: scene(img, t, dur, ctx)}
  - 绘图基元全部收进 SceneAPI，题材包不直接碰 Pillow 底层
  - 数学内核由题材包自己提供（先算对，再画对）
  - WSL/Linux 环境自适应：字体探测、ffmpeg 由 imageio-ffmpeg 提供
"""
import hashlib
import math
import os

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------- 主题

W, H, FPS = 1080, 1920, 30

# ---------------------------------------------------------------- 设计系统 token
# 唯一出处：docs/visual-system.md；角色 skill：skills/xhs-art-director/SKILL.md
# 视频 deck / 封面 / 一张图三处都从这里取色与取字号，不许各写一份。
#
# 色板纪律（"一个角色一个色"，靠用途边界分开，不靠色相数量）：
#   BRAND  青  —— 结构：栏目名、轴、进度条、封面副标/胶囊、序号环。永不表示"错误/点亮"
#   STATE  玫  —— 状态：点亮/当前/冲突/翻倍；封面序号徽标、F3 标注圈。永不表示"结构"
#   AMBER  琥珀 —— 高亮：每屏最多一处关键词/关键数字；封面词块必须深字压上去
#   GREEN  绿  —— 达标：只在与"不达标"对照时出现
#   INK    海军蓝中性阶 —— 底、面板、描边、四级文字
# 为什么只有一条红：旧的 runtime.TILE_ON #FF5274 与 cover.ACCENT #FF2D55 色相只差 1°、
# RGB 差 48 —— 既不像"同一个色"，也不像"两个色"，是缩略图上最难受的那种"脏"。
INK = {
    "950": (4, 6, 14),      # 最深底 / 亮底上的深字
    "900": (10, 14, 26),    # 面板
    "850": (12, 17, 32),    # 视频底（顶）
    "800": (18, 25, 43),    # 瓦片 / 卡片
    "700": (28, 38, 62),    # 抬升面
    "600": (46, 60, 96),    # 描边 / 分隔（非文字）
    "550": (60, 74, 108),   # 未激活填充 / 装饰（非文字）
    "500": (74, 90, 130),   # 极弱：仅图形，对比 2.7:1 —— 不许承载文字
    "400": (128, 146, 180), # 三级文字（辅助信息，5.99:1）
    "300": (154, 168, 196), # 次级文字（注脚/标签，7.86:1）
    "200": (186, 198, 222), # 次级文字（浅）
    "100": (214, 224, 240), # 强调文字
    "50": (236, 242, 255),  # 正文
    "0": (255, 255, 255),   # 主标题
}
BRAND = {                   # 青（H187）
    "700": (12, 96, 118),   # 深填色：压白字 7.12:1（唯一可以压白字的青）
    "600": (18, 140, 168),  # 底图光斑 / 压深字 5.15:1（白字只有 3.93，别用）
    "500": (61, 232, 255),  # 品牌主色：深底上的文字 12.70:1；填色压深字 13.67:1
    "400": (150, 244, 255), # 提亮
}
STATE = {                   # 玫（H348）—— 每处只用一条红
    "700": (168, 16, 52),   # 深填：压白字 7.53:1
    "600": (214, 24, 68),   # 压白字 5.16:1
    "500": (255, 61, 99),   # 深底上的文字 5.46:1；填色压深字 5.88:1（压白字只有 3.44，别用）
    "400": (255, 148, 170), # 描边/提亮
}
AMBER_SCALE = {             # 琥珀（H42）—— 每屏一处
    "600": (214, 148, 20),  # 底图环境光（不抢高亮块的位）
    "500": (255, 194, 46),  # 高亮块填充：压 INK.950 深字 12.54:1；当文字 11.65:1
    "400": (255, 214, 120),
}
GREEN_SCALE = {             # 绿（H153）—— 达标
    "600": (24, 160, 100),  # 压深字 6.02:1
    "500": (54, 227, 148),  # 当文字 11.26:1；填色压深字 12.12:1
}
# 底图光斑专用（只出现在 background glow，永远不作为文字色）
GLOW = {"indigo": (86, 110, 220), "ember": (188, 70, 96)}

# 排版刻度：1.2 比例，偶数取整。1080 宽画布，240px 信息流 ≈ ×0.222
# 可读性下限（按 240px 缩略图折算）：承载信息的字 ≥ body(58) ≈ 12.9px；
# 有底色衬托的胶囊/标签 ≥ label(48) ≈ 10.7px；micro/caption 只许装饰，不许承载信息。
TS = {"micro": 32, "caption": 40, "label": 48, "body": 58, "lead": 68,
      "title": 84, "h3": 100, "h2": 120, "h1": 144, "display": 172, "mega": 208}
LEADING = {"display": 1.10, "title": 1.14, "body": 1.45, "caption": 1.30}
SPACE = {"xs": 8, "sm": 16, "md": 24, "lg": 40, "xl": 64, "xxl": 96}
GUTTER = 72                 # 画布安全边距（平台 UI 裁切线之外）

# ---- 语义别名（历史名保留，别处不许再写死颜色）
BG_TOP = INK["850"]
BG_BOT = INK["950"]
TILE_OFF = INK["800"]
TILE_OFF_EDGE = INK["600"]
TILE_ON = STATE["500"]
TILE_ON_EDGE = STATE["400"]
ACCENT = BRAND["500"]
AMBER = AMBER_SCALE["500"]
GREEN = GREEN_SCALE["500"]
TEXT = INK["50"]
DIM = INK["300"]
FAINT = INK["550"]

# ---------------------------------------------------------------- 主题切换
# 语义 token 的具名变体见 shortvideo/themes.py（8 套：5 深 3 浅）。
# 为什么用"重绑模块全局"而不是传参：全库 100+ 处都是 `R.ACCENT` 这种活引用，
# 重绑一次就整体换肤；只有 lightsout/sorting 写过 `from runtime import ACCENT`（已改）。
from .themes import THEMES, theme_for_video  # noqa: E402

_CURRENT_THEME = "midnight"
LIGHT = False


def theme_names():
    return list(THEMES)


def current_theme():
    return _CURRENT_THEME


def set_theme(name):
    """切换配色主题。name 为空/未知 → 回到 midnight（不报错，渲染不该因配色挂掉）。"""
    global _CURRENT_THEME, LIGHT
    global INK, BRAND, STATE, AMBER_SCALE, GREEN_SCALE, GLOW
    global BG_TOP, BG_BOT, TILE_OFF, TILE_OFF_EDGE, TILE_ON, TILE_ON_EDGE
    global ACCENT, AMBER, GREEN, TEXT, DIM, FAINT, SUB_INK, ON_BLOCK
    key = name if name in THEMES else "midnight"
    t = THEMES[key]
    _CURRENT_THEME = key
    LIGHT = bool(t["light"])
    INK = dict(t["ink"])
    BRAND = dict(t["brand"])
    STATE = dict(t["state"])
    AMBER_SCALE = dict(t["amber"])
    GREEN_SCALE = dict(t["green"])
    GLOW = dict(t["glow"])
    BG_TOP, BG_BOT = INK["850"], INK["950"]
    TILE_OFF, TILE_OFF_EDGE = INK["800"], INK["600"]
    TILE_ON, TILE_ON_EDGE = STATE["500"], STATE["400"]
    ACCENT = BRAND["500"]
    AMBER = AMBER_SCALE["500"]
    GREEN = GREEN_SCALE["500"]
    TEXT = INK["50"]
    DIM = INK["300"]
    FAINT = INK["550"]
    # 副标：深底用浅灰，浅底用中深灰（浅色主题不能再用 #CED8E8，那样是白底白字）
    SUB_INK = INK["200"] if LIGHT else (206, 216, 232)
    # 压在高亮块/强调色块上的字：深色主题的块是亮的 → 压深字；浅色主题的块是深的 → 压白字。
    # 写死一种必然在某一半主题上不可读（浅色主题的靛蓝块压近黑字只有 1.6:1）。
    ON_BLOCK = (255, 255, 255) if LIGHT else INK["950"]
    return key


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


_NOTDEF = {}


def _glyph_sig(f, ch):
    m = f.getmask(ch)
    return hashlib.md5(bytes(m)).hexdigest()


def missing_glyphs(text, size=80, bold=False):
    """返回 text 里**字体缺字形**的字符列表。

    原理：缺字形时 FreeType 回落到 .notdef（一个方框/空白），
    所以拿私用区字符 U+E000 的掩码当"缺字形指纹"，逐个比对即可。

    实测这套字体：₀（U+2080）缺，₁₂ 有；希腊字母 α~ω 全有。
    静默失败很坑——`μ₀` 会渲染成 `μ?`，肉眼在缩略图上根本看不出来。
    """
    f = font(size, bold=bold)
    key = (size, bold)
    if key not in _NOTDEF:
        _NOTDEF[key] = _glyph_sig(f, "\ue000")
    ref = _NOTDEF[key]
    seen, out = set(), []
    for ch in str(text or ""):
        if ch in seen or ch.isspace():
            continue
        seen.add(ch)
        if _glyph_sig(f, ch) == ref:
            out.append(ch)
    return out


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
