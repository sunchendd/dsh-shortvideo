"""AI 画室：纯程序化生成底图/氛围图/几何插画（无生成式模型，秒级出图）。

用途：
  - 给封面、视频片头、一张图海报提供高质量底图（比纯色渐变高级得多）
  - 生成"几何主视觉"，配合 SceneAPI 的 panel/kinetic_text 用
样式：
  aurora  极光：大色团+柔光+颗粒，适合悬疑/重磅结论
  mesh    Mesh 渐变：多点径向混合，干净高级，适合知识科普
  rays    放射光束：爆款封面那种"聚光"感
  grid    科技网格：细线网格+光斑+扫描线
  paper   浅色纸张：米白+细纹，适合图文笔记

用法：
  python art.py --style mesh --w 1080 --h 1440 --out out/bg.png
  python art.py --style rays --accent 3de8ff --seed 7 --out out/rays.png
  python art.py --list
"""
import argparse
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from shortvideo import runtime as R

STYLES = ("aurora", "mesh", "rays", "grid", "paper")

# 深底配色：全部从 runtime 的设计 token 推出来（docs/visual-system.md §4）
# 纪律：底图只许用"品牌青族 + 中性阶"，**红（STATE）与亮黄（AMBER.500）留给前景**——
# 底图一旦发光到黄/红，封面/一张图上的高亮词块就没有"唯一一处暖色"的特权了，
# 这是旧 PALETTES 里 warm/default 都掺琥珀造成的"什么都想亮"。
# 四套名字保留（content.json / oneimage spec 里在用），只换成同族的深浅变体。
PALETTES = {
    # 均衡：与视频底同一族（青 主 + 靛 辅）
    "default": (R.INK["900"], R.INK["950"],
                (R.BRAND["500"], R.BRAND["600"], R.GLOW["indigo"])),
    # 冷调：整体往靛/深蓝压，适合"精密/数据"题材
    "cool": ((7, 10, 24), R.INK["950"],
             (R.GLOW["indigo"], R.BRAND["600"], R.BRAND["500"])),
    # 暖调：只用"余烬"级的玫与暗琥珀当环境光，明度压到不抢高亮块
    "warm": ((26, 12, 18), (8, 4, 8),
             (R.GLOW["ember"], R.AMBER_SCALE["600"], R.BRAND["600"])),
    # 纸张：浅底图文笔记（文字靠 INK.900 压上去）
    "paper": ((246, 244, 238), (230, 228, 220),
              (R.INK["900"], R.BRAND["600"], R.GLOW["ember"])),
}


def _radial(w, h, cx, cy, r, color, strength=1.0):
    """径向柔光（复用 runtime 的蒙版缓存，避免逐像素 Python 计算）。"""
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    rr = max(8, int(r))
    mask = R._glow_mask(rr).point(lambda v: int(v * min(1.0, strength)))  # noqa: SLF001
    sprite = Image.new("RGBA", (rr * 2, rr * 2), tuple(color) + (255,))
    img.paste(sprite, (int(cx - rr), int(cy - rr)), mask)
    return img


def _grain(img, sigma=6.0, opacity=0.10):
    """胶片颗粒：PIL 的 effect_noise 是 C 实现，整帧只要几毫秒。"""
    noise = Image.effect_noise(img.size, sigma).convert("L")
    noise = Image.merge("RGB", (noise, noise, noise))
    return Image.blend(img, noise, opacity)


def _vignette(img, power=1.6):
    w, h = img.size
    grad = Image.radial_gradient("L").resize((w, h))
    dark = Image.new("RGB", (w, h), (0, 0, 0))
    return Image.composite(dark, img, grad.point(lambda v: int((v / 255) ** power * 170)))


def _glow_field(w, h, spots, blur=None):
    """把若干径向光斑叠加成一张 RGB 光场（加法混合，颜色更亮更"通透"）。"""
    field = Image.new("RGB", (w, h), (0, 0, 0))
    for (cx, cy, r, color, strength) in spots:
        layer = _radial(w, h, cx, cy, r, color, strength).convert("RGB")
        field = ImageChops.add(field, layer)
    if blur:
        field = field.filter(ImageFilter.GaussianBlur(int(blur)))
    return field


def _grad(top, bot, w, h):
    g = Image.linear_gradient("L").resize((w, h))
    return Image.composite(Image.new("RGB", (w, h), bot), Image.new("RGB", (w, h), top), g)


# ---------------------------------------------------------------- 各风格

def aurora(w, h, pal, seed):
    top, bot, (c1, c2, c3) = pal
    rnd = random.Random(seed)
    spots = [(w * bx + rnd.uniform(-60, 60), h * by + rnd.uniform(-80, 80),
              int(min(w, h) * br), col, 0.95)
             for bx, by, br, col in ((0.15, 0.12, 0.7, c1), (0.9, 0.28, 0.65, c2),
                                     (0.45, 0.9, 0.75, c3))]
    field = _glow_field(w, h, spots, blur=min(w, h) * 0.07)
    img = ImageChops.add(_grad(top, bot, w, h), field)
    return _grain(_vignette(img, 1.8), 7.0, 0.10)


def mesh(w, h, pal, seed):
    top, bot, (c1, c2, c3) = pal
    rnd = random.Random(seed)
    cols = [c1, c2, c3, c1, c2, c3]
    # Mesh 用 alpha 叠加（而不是加法）：加法会把青+粉+琥珀混成灰白，色域反而脏
    img = _grad(top, bot, w, h).convert("RGBA")
    # 少而大、边界柔和的色盘（不要 6 团叠一起，否则混成脏棕）
    for col in cols[:3]:
        cx = w * rnd.choice((0.18, 0.5, 0.82)) + rnd.uniform(-w * 0.06, w * 0.06)
        cy = h * rnd.choice((0.2, 0.55, 0.85)) + rnd.uniform(-h * 0.05, h * 0.05)
        r = int(min(w, h) * 0.55)
        layer = _radial(w, h, cx, cy, r, col, 0.95)
        img = Image.alpha_composite(img, layer.filter(
            ImageFilter.GaussianBlur(int(min(w, h) * 0.02))))
    img = img.convert("RGB")
    return _grain(_vignette(img, 1.1), 4.0, 0.05)


def rays(w, h, pal, seed):
    """放射光束：从底部中心发散，配一层底光。"""
    top, bot, (c1, c2, _c3) = pal
    img = _grad(top, bot, w, h).convert("RGBA")
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    rnd = random.Random(seed)
    cx, cy = w * rnd.uniform(0.35, 0.65), h * 1.02
    n = 14
    for i in range(n):
        ang0 = math.pi * (1.0 + i / n) + rnd.uniform(-0.02, 0.02)
        ang1 = ang0 + math.pi / n * rnd.uniform(0.25, 0.62)
        col = c1 if i % 2 == 0 else c2
        alpha = rnd.randint(26, 64)
        L = h * 1.6
        d.polygon([(cx, cy),
                   (cx + L * math.cos(ang0), cy + L * math.sin(ang0)),
                   (cx + L * math.cos(ang1), cy + L * math.sin(ang1))],
                  fill=tuple(col) + (alpha,))
    layer = layer.filter(ImageFilter.GaussianBlur(int(min(w, h) * 0.012)))
    img = Image.alpha_composite(img, layer)
    img = Image.alpha_composite(img, _radial(w, h, cx, cy - h * 0.12, int(w * 0.55), c1, 0.45)
                               .filter(ImageFilter.GaussianBlur(int(w * 0.05))))
    return _grain(_vignette(img.convert("RGB"), 1.8), 6.0, 0.10)


def grid(w, h, pal, seed):
    top, bot, (c1, c2, _c3) = pal
    img = _grad(top, bot, w, h).convert("RGBA")
    d = ImageDraw.Draw(img, "RGBA")
    step = max(40, w // 18)
    for x in range(0, w, step):
        d.line([(x, 0), (x, h)], fill=(255, 255, 255, 12), width=1)
    for y in range(0, h, step):
        d.line([(0, y), (w, y)], fill=(255, 255, 255, 12), width=1)
    rnd = random.Random(seed)
    for _ in range(3):
        img = Image.alpha_composite(
            img, _radial(w, h, rnd.uniform(0, w), rnd.uniform(0, h),
                         int(min(w, h) * rnd.uniform(0.3, 0.5)), rnd.choice([c1, c2]), 0.4))
    # 扫描线
    scan = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scan)
    for y in range(0, h, 6):
        sd.line([(0, y), (w, y)], fill=(0, 0, 0, 26), width=2)
    img = Image.alpha_composite(img, scan)
    return _grain(_vignette(img.convert("RGB"), 1.5), 3.0, 0.05)


def paper(w, h, pal, seed):
    top, bot, (ink, c1, c2) = pal
    img = _grad(top, bot, w, h).convert("RGBA")
    rnd = random.Random(seed)
    for _ in range(2):
        img = Image.alpha_composite(
            img, _radial(w, h, rnd.uniform(0, w), rnd.uniform(0, h), int(w * 0.35),
                         rnd.choice([c1, c2]), 0.18))
    d = ImageDraw.Draw(img, "RGBA")
    # 手账风：细横线 + 页边距线
    for y in range(int(h * 0.08), h, max(48, h // 26)):
        d.line([(int(w * 0.06), y), (int(w * 0.94), y)], fill=tuple(ink) + (26,), width=2)
    d.line([(int(w * 0.055), 0), (int(w * 0.055), h)], fill=tuple(c2) + (110,), width=3)
    return _grain(img.convert("RGB"), 3.0, 0.05)


_BUILDERS = {"aurora": aurora, "mesh": mesh, "rays": rays, "grid": grid, "paper": paper}


def make(style="mesh", w=1080, h=1440, seed=0, palette="default"):
    if style not in _BUILDERS:
        raise SystemExit(f"未知风格 {style}；可选：{', '.join(STYLES)}")
    pal = PALETTES.get(palette) or PALETTES["default"]
    return _BUILDERS[style](w, h, pal, seed)


def main():
    ap = argparse.ArgumentParser(description="程序化底图/氛围图生成器（AI 画室）")
    ap.add_argument("--style", default="mesh", choices=STYLES)
    ap.add_argument("--w", type=int, default=1080)
    ap.add_argument("--h", type=int, default=1440)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--palette", default="default", choices=tuple(PALETTES))
    ap.add_argument("--out", default=None)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        print("styles:", ", ".join(STYLES))
        print("palettes:", ", ".join(PALETTES))
        return
    out = args.out or f"out/art_{args.style}_{args.palette}.png"
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img = make(args.style, args.w, args.h, args.seed, args.palette)
    img.save(out)
    print(f"[ok] {out}  {args.w}x{args.h}  style={args.style} palette={args.palette} seed={args.seed}")


if __name__ == "__main__":
    main()
