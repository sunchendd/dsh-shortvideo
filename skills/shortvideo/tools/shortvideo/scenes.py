"""SceneAPI：题材包可用的全部绘图基元。

约定：所有方法第一个参数是当前帧画布（RGBA，1080x1920），
时间参数 t 为镜头内本地秒数。题材包永远通过 SceneAPI 画图。

性能约定（不改画面，只省分配）：
  - 需要半透明叠加的小部件（tag / caption / ring / arrow）不再开 1080x1920 全帧
    图层，而是按自身包围盒开小图层再合成；偏移取整，栅格化结果与画在整帧上一致。
  - 纯色文字/线条直接画在当前帧上（Pillow 的 "RGBA" 画笔自带混合），不做整帧合成。
"""
import math

from PIL import Image, ImageChops, ImageDraw

from . import runtime as R

# 文本折行缓存：字幕/标题在相邻帧大量重复，getlength 逐字测量没必要重算
_wrap_cache = {}
# 文字小贴图缓存：(文本, 字号, 颜色, 相位, ...) -> (RGBA 贴图, 相对偏移)
# CJK 字形栅格化是整帧渲染里最贵的单项之一（大字号 5 个字≈0.8ms），
# 同一段文字在相邻帧反复出现，渲染一次缓存起来即可。
_text_cache = {}
_text_cache_bytes = [0]  # 蒙版缓存占用（单字节/像素），防止长视频里无限涨
# 字幕条图层缓存：内容只取决于 (文本, alpha, y, 画布宽)
_caption_cache = {}


def _layer(box, pad=2):
    """按绝对包围盒 (x0, y0, x1, y1) 开一张透明小图层。

    返回 (layer, ox, oy)：ox/oy 是图层左上角在画布上的整数坐标，
    画图时局部坐标 = 绝对坐标 - (ox, oy)，整数平移不改变抗锯齿结果。
    """
    x0, y0, x1, y1 = box
    ox, oy = math.floor(x0) - pad, math.floor(y0) - pad
    w = math.ceil(x1) + pad - ox + 1
    h = math.ceil(y1) + pad - oy + 1
    return Image.new("RGBA", (w, h), (0, 0, 0, 0)), ox, oy


def _blend(img, layer, ox, oy):
    """把小图层合成回画布（越界自动裁剪）。"""
    lw, lh = layer.size
    x0, y0 = max(0, ox), max(0, oy)
    x1, y1 = min(img.size[0], ox + lw), min(img.size[1], oy + lh)
    if x1 <= x0 or y1 <= y0:
        return
    if (x0 - ox, y0 - oy, x1 - ox, y1 - oy) != (0, 0, lw, lh):
        # alpha_composite 自身不裁剪：负坐标/越界会静默算错，这里先裁源
        layer = layer.crop((x0 - ox, y0 - oy, x1 - ox, y1 - oy))
    img.alpha_composite(layer, (x0, y0))


_SOLID_CACHE = {}


def _text_mask(s, size, bold, math_, anchor, phase):
    """把一段文字栅格化成覆盖率蒙版并缓存，返回 (mask, ox, oy)。

    - 键里带亚像素相位 phase=(x 小数, y 小数)：Pillow 对文字做亚像素定位，
      相位不同画面细节不同，相位必须参与缓存键，才能与直接 d.text 逐像素一致。
    - 贴图内一律在正坐标处绘制：Pillow 对负坐标的取整方式与正数不同，
      直接在负局部坐标上栅格化会整像素偏移。
    - 只缓存覆盖率（L 蒙版）+ 颜色贴图，颜色/透明度每次用 paste(mask) 合成：
      Pillow 的 "RGBA" 画笔把 ink 的 alpha 直接写进画布 alpha 通道（并不参与 RGB 混合），
      等价于 paste(纯色贴图, 蒙版)，用 alpha_composite 会得到不同的半透明效果。
    """
    key = (s, size, bold, math_, anchor, phase)
    hit = _text_cache.get(key)
    if hit is not None:
        return hit
    f = R.font(size, bold, math_)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    bx0, by0, bx1, by1 = probe.textbbox(phase, s, font=f, anchor=anchor)  # 以锚点为原点的墨迹框
    px, py = math.ceil(2 - bx0), math.ceil(2 - by0)  # 贴图内锚点保证落在 >=2 的正坐标
    w = max(math.ceil(px + bx1) + 3, 8)
    h = max(math.ceil(py + by1) + 3, 8)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(canvas, "RGBA").text((px + phase[0], py + phase[1]), s, font=f,
                                        fill=(255, 255, 255, 255), anchor=anchor)
    ink = canvas.getbbox()  # 按真实墨迹裁剪，比字体度量更保险
    if ink is None:
        out = (None, 0, 0)
    else:
        out = (canvas.crop(ink).getchannel("A"), ink[0] - px, ink[1] - py)
    _text_cache_bytes[0] += 0 if out[0] is None else out[0].size[0] * out[0].size[1]
    if _text_cache_bytes[0] > 24 << 20 or len(_text_cache) > 512:
        # 动画里的相位/字号千变万化，缓存要有上限；清空后静态文字重建一次即可
        _text_cache.clear()
        _text_cache_bytes[0] = 0 if out[0] is None else out[0].size[0] * out[0].size[1]
    _text_cache[key] = out
    return out


def _solid(rgba, size):
    """纯色 RGBA 贴图缓存（paste(mask) 的源图，尺寸需与蒙版一致）。"""
    key = (rgba, size)
    hit = _SOLID_CACHE.get(key)
    if hit is None:
        hit = Image.new("RGBA", size, rgba)
        if len(_SOLID_CACHE) > 256:
            _SOLID_CACHE.clear()
        _SOLID_CACHE[key] = hit
    return hit


def _text(img, xy, s, size, fill, bold=False, math_=False, anchor="mm", alpha=1.0):
    """统一的文字绘制入口：缓存覆盖率蒙版，颜色/透明度每次合成，输出与 d.text 一致。

    注意：这里不做 alpha<=0 提前返回。Pillow 的 "RGBA" 画笔只把 ink 的 alpha 写进
    画布 alpha 通道，RGB 仍然被覆盖率混合——也就是说 alpha=0 的文字在转 RGB 时依然
    可见（formula() 正依赖这一行为）。要不要跳过由调用方自己判断。
    """
    s = R.sanitize(s, bold=bold)
    if not s:
        return
    fill = tuple(fill)
    if len(fill) == 4:  # 调用方已算好 alpha（bar_chart / matrix 这类）
        rgba = fill
    else:
        rgba = fill[:3] + (int(255 * R.clamp(alpha)),)
    x, y = xy
    fx, fy = math.floor(x), math.floor(y)
    phase = (round(x - fx, 4), round(y - fy, 4))
    mask, ox, oy = _text_mask(s, size, bold, math_, anchor, phase)
    if mask is not None:
        img.paste(_solid(rgba, mask.size), (fx + ox, fy + oy), mask)


class SceneAPI:
    # ------------------------------------------------------------ 文本

    @staticmethod
    def text(img, xy, s, size, fill=R.TEXT, bold=False, math=False, anchor="mm", alpha=1.0):
        if alpha <= 0.01:
            return
        _text(img, xy, s, size, fill, bold, math, anchor, alpha)

    @staticmethod
    def title(img, kicker, main, alpha=1.0, y=300):
        SceneAPI.text(img, (R.W / 2, y), kicker, 36, R.ACCENT, bold=True, alpha=alpha * 0.9)
        if main:
            SceneAPI.text(img, (R.W / 2, y + 74), main, 62, R.TEXT, bold=True, alpha=alpha)

    @staticmethod
    def wrap(s, size, max_w, bold=False):
        key = (s, size, max_w, bold)
        cached = _wrap_cache.get(key)
        if cached is not None:
            return list(cached)
        f = R.font(size, bold)
        lines, cur = [], ""
        for ch in s:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if f.getlength(cur + ch) > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        if len(_wrap_cache) > 256:
            _wrap_cache.clear()
        _wrap_cache[key] = tuple(lines)
        return lines

    @staticmethod
    def formula(img, cx, cy, parts, alpha=1.0):
        """parts: [(文本, 字号, 颜色, 是否数学体, dy)]，居中拼接，dy 负值=上标。"""
        widths = [R.font(size, math=m).getlength(s) for s, size, _c, m, _d in parts]
        x = cx - sum(widths) / 2
        for (s, size, color, m, dy), w in zip(parts, widths):
            _text(img, (x, cy + dy), s, size, color, math_=m, anchor="lm", alpha=alpha)
            x += w

    @staticmethod
    def _emphasis_runs(text, words):
        """把字幕切成 [(片段, 是否高亮)]；words 为空或没命中时返回单段（走原路径）。"""
        if not words:
            return [(text, False)]
        hits = []
        for w in words:
            if not w:
                continue
            start = 0
            while True:
                k = text.find(w, start)
                if k < 0:
                    break
                hits.append((k, k + len(w)))
                start = k + len(w)
        if not hits:
            return [(text, False)]
        hits.sort()
        merged, cur = [], hits[0]
        for a, b in hits[1:]:
            if a <= cur[1]:
                cur = (cur[0], max(cur[1], b))
            else:
                merged.append(cur)
                cur = (a, b)
        merged.append(cur)
        runs, pos = [], 0
        for a, b in merged:
            if a > pos:
                runs.append((text[pos:a], False))
            runs.append((text[a:b], True))
            pos = b
        if pos < len(text):
            runs.append((text[pos:], False))
        return runs

    @staticmethod
    def caption(img, s, alpha=1.0, y=1645, size=50, emphasis=None,
                hi_color=None, hi_scale=1.0):
        """底部字幕卡。emphasis=[词...] 时这些词用强调色（可加粗放大）绘制。"""
        if not s or alpha <= 0.01:
            return
        s = R.sanitize(s, bold=True)
        emphasis = [R.sanitize(w, bold=True) for w in emphasis] if emphasis else emphasis
        a = round(R.clamp(alpha), 2)
        runs = SceneAPI._emphasis_runs(s, emphasis)
        hi = tuple(hi_color or R.AMBER)
        # 无高亮：沿用原来的整条图层缓存（逐像素与旧版一致）
        if len(runs) == 1 and not runs[0][1]:
            key = (s, a, y, R.W, size, None)
            hit = _caption_cache.get(key)
            if hit is None:
                lines = SceneAPI.wrap(s, size, 880, bold=True)
                line_h = int(size * 1.42)
                h = line_h * len(lines) + 44
                layer, ox, oy = _layer(((R.W - 940) / 2, y - 22, (R.W + 940) / 2, y - 22 + h))
                d = ImageDraw.Draw(layer)
                d.rounded_rectangle(((R.W - 940) / 2 - ox, y - 22 - oy,
                                     (R.W + 940) / 2 - ox, y - 22 + h - oy),
                                    radius=28, fill=R.CAPTION_BG + (int(R.CAPTION_BG_A * a),),
                                    outline=R.ACCENT + (int(60 * a),), width=2)
                for i, ln in enumerate(lines):
                    d.text((R.W / 2 - ox, y + 10 + line_h * i + line_h / 2 - oy), ln,
                           font=R.font(size, bold=True), fill=R.TEXT + (int(255 * a),), anchor="mm")
                hit = (layer, ox, oy)
                if len(_caption_cache) > 24:
                    _caption_cache.clear()
                _caption_cache[key] = hit
            _blend(img, hit[0], hit[1], hit[2])
            return

        # 含高亮：按字符偏移映射到折行后的每一行，逐段绘制（高亮段可用不同字号/颜色）
        plain = "".join(seg for seg, _hi in runs)
        key = (plain, tuple(emphasis or ()), a, y, R.W, size, hi_scale)
        hit = _caption_cache.get(key)
        if hit is None:
            lines = SceneAPI.wrap(plain, size, 880, bold=True)
            line_h = int(size * 1.42)
            h = line_h * len(lines) + 44
            layer, ox, oy = _layer(((R.W - 940) / 2, y - 22, (R.W + 940) / 2, y - 22 + h))
            d = ImageDraw.Draw(layer)
            d.rounded_rectangle(((R.W - 940) / 2 - ox, y - 22 - oy,
                                 (R.W + 940) / 2 - ox, y - 22 + h - oy),
                                radius=28, fill=R.CAPTION_BG + (int(R.CAPTION_BG_A * a),),
                                outline=R.ACCENT + (int(60 * a),), width=2)
            flags = [hi for _seg, hi in runs]
            starts, pos = [], 0
            for seg, _hi in runs:
                starts.append(pos)
                pos += len(seg)
            off = 0
            for i, ln in enumerate(lines):
                cy = y + 10 + line_h * i + line_h / 2 - oy
                # 该行在 plain 中的字符区间 [off, off+len(ln))
                x = R.W / 2 - R.font(size, bold=True).getlength(ln) / 2 - ox
                k = 0
                while k < len(ln):
                    idx = off + k
                    hi_here = False
                    for (a0, (seg, is_hi)), st in zip(enumerate(runs), starts):
                        if st <= idx < st + len(seg):
                            hi_here = is_hi
                            break
                    j = k + 1
                    while j < len(ln):
                        idx2 = off + j
                        same = False
                        for (seg, is_hi), st in zip(runs, starts):
                            if st <= idx2 < st + len(seg):
                                same = (is_hi == hi_here)
                                break
                        if not same:
                            break
                        j += 1
                    piece = ln[k:j]
                    fnt = R.font(int(size * (hi_scale if hi_here else 1.0)), bold=True)
                    d.text((x, cy), piece, font=fnt, anchor="lm",
                           fill=(hi if hi_here else R.TEXT) + (int(255 * a),))
                    x += fnt.getlength(piece)
                    k = j
                off += len(ln)
            hit = (layer, ox, oy)
            if len(_caption_cache) > 24:
                _caption_cache.clear()
            _caption_cache[key] = hit
        _blend(img, hit[0], hit[1], hit[2])

    @staticmethod
    def tag(img, cx, cy, s, color=R.ACCENT, alpha=1.0, size=34):
        if alpha <= 0.01:
            return
        a = R.clamp(alpha)
        f = R.font(size, bold=True)
        w = f.getlength(s) + 46
        half_h = size * 1.05
        left, top = cx - w / 2, cy - half_h
        # 图层内容只取决于 (文本, 颜色, alpha, 字号, 绘制相位的亚像素小数)；
        # sorting 的柱状图每帧要画 6~7 个标签，命中缓存后只剩一次小区域合成。
        # 注意相位要取真正用于栅格化的 left/top 的小数部分：整数平移不改变画面，
        # 小数不同则抗锯齿不同，必须分开缓存。
        key = ("tag", s, tuple(color), a, size,
               round(left - math.floor(left), 4), round(top - math.floor(top), 4))
        layer = _caption_cache.get(key)
        if layer is None:
            layer, ox, oy = _layer((left, top, cx + w / 2, cy + half_h))
            d = ImageDraw.Draw(layer)
            d.rounded_rectangle((left - ox, top - oy, cx + w / 2 - ox, cy + half_h - oy),
                                radius=int(size * 1.05), fill=color + (int(38 * a),),
                                outline=color + (int(150 * a),), width=2)
            d.text((cx - ox, cy - oy), s, font=f, fill=color + (int(255 * a),), anchor="mm")
            if len(_caption_cache) > 24:
                _caption_cache.clear()
            _caption_cache[key] = layer
        # 合成位置每次按当前坐标重算（图层内只差一个整数偏移）
        _blend(img, layer, math.floor(left) - 2, math.floor(top) - 2)

    # ------------------------------------------------------------ 网格

    @staticmethod
    def grid(img, lights, top=760, size=196, gap=26, press=None, highlight=None,
             hl_color=R.AMBER, labels=None):
        """lights: [0..1]*9 亮度；press: {idx: 0..1}；labels: 每格中央文字。"""
        press = press or {}
        n = 3
        total = n * size + (n - 1) * gap
        x0 = (R.W - total) // 2
        rects = []
        d = ImageDraw.Draw(img)
        for i in range(n * n):
            r, c = divmod(i, n)
            x = x0 + c * (size + gap)
            y = top + r * (size + gap)
            rect = (x, y, x + size, y + size)
            rects.append(rect)
            lit = lights[i] if i < len(lights) else 0.0
            p = press.get(i, 0.0)
            cx, cy = (x + size / 2, y + size / 2)
            scale = 1.0 - 0.06 * p
            bw, bh = size * scale, size * scale
            box = (cx - bw / 2, cy - bh / 2, cx + bw / 2, cy + bh / 2)
            if lit > 0.02:
                R.glow(img, cx, cy, int(size * 0.95), R.TILE_ON, strength=0.9 * lit)
            fill = tuple(int(R.lerp(R.TILE_OFF[k], R.TILE_ON[k], lit)) for k in range(3))
            edge = tuple(int(R.lerp(R.TILE_OFF_EDGE[k], R.TILE_ON_EDGE[k], lit)) for k in range(3))
            d.rounded_rectangle(box, radius=26, fill=fill, outline=edge, width=3)
            if labels:
                _text(img, (cx, cy), labels[i], int(size * 0.34), 
                      (255, 255, 255) if lit > 0.5 else R.FAINT, bold=True)
        if highlight:
            for i in highlight:
                x, y, x1, y1 = rects[i]
                d.rounded_rectangle((x - 6, y - 6, x1 + 6, y1 + 6), radius=32,
                                    outline=hl_color, width=5)
        return rects

    @staticmethod
    def matrix(img, cells, n, cx, cy, cell=56, gap=8, alpha=1.0, reveal=1.0,
               hi_rows=None, hi_cols=None, hi_color=R.AMBER, label=None):
        """cells: n 行 n 列（列表的列表）；reveal: 逐格浮现 0-1。"""
        hi_rows = hi_rows or set()
        hi_cols = hi_cols or set()
        total = n * cell + (n - 1) * gap
        x0, y0 = cx - total / 2, cy - total / 2
        d = ImageDraw.Draw(img, "RGBA")
        a = int(255 * R.clamp(alpha))
        bw, pad = 4, 16
        for sx in (x0 - pad, x0 + total + pad - bw):
            d.rectangle((sx, y0, sx + bw, y0 + total), fill=R.DIM + (a,))
        count = n * n
        for r in range(n):
            for c in range(n):
                k = r * n + c
                local = R.clamp(reveal * count - k)
                if local <= 0:
                    continue
                x, y = x0 + c * (cell + gap), y0 + r * (cell + gap)
                v = cells[r][c]
                if r in hi_rows or c in hi_cols:
                    d.rounded_rectangle((x, y, x + cell, y + cell), radius=8,
                                        fill=hi_color + (int(60 * local),),
                                        outline=hi_color + (int(210 * local),), width=2)
                else:
                    d.rounded_rectangle((x, y, x + cell, y + cell), radius=8,
                                        fill=(255, 255, 255, int(12 * local)))
                _text(img, (x + cell / 2, y + cell / 2), str(v), int(cell * 0.58),
                      ((236, 242, 255) if v else (118, 136, 168)) + (int(a * local),),
                      bold=bool(v), math_=True)
        if label:
            _text(img, (cx, y0 - 34), label, 34, R.DIM + (a,), bold=True)
        return (x0, y0, x0 + total, y0 + total)

    # ------------------------------------------------------------ 数据图形

    @staticmethod
    def bar_chart(img, bars, cx, cy, max_w=760, bar_h=64, gap=18, labels=None,
                  alpha=1.0, grow=None):
        """bars: [0..1]；grow: 整体生长动画 0-1。返回每根条的矩形。"""
        d = ImageDraw.Draw(img, "RGBA")
        a = R.clamp(alpha)
        grow = R.clamp(grow if grow is not None else 1.0)
        n = len(bars)
        total_h = n * bar_h + (n - 1) * gap
        y = cy - total_h / 2
        rects = []
        for i, v in enumerate(bars):
            w = max_w * v * grow
            rect = (cx - max_w / 2, y, cx - max_w / 2 + w, y + bar_h)
            d.rounded_rectangle(rect, radius=bar_h // 2,
                                fill=(61, 232, 255, int(200 * a)) if i % 2 == 0
                                else (255, 82, 116, int(200 * a)))
            if labels:
                _text(img, (cx - max_w / 2 + 18, y + bar_h / 2), labels[i],
                      int(bar_h * 0.5), (10, 14, 26, int(255 * a)), bold=True, anchor="lm")
            rects.append(rect)
            y += bar_h + gap
        return rects

    @staticmethod
    def arrow(img, p0, p1, color=R.ACCENT, width=5, alpha=1.0, head=18):
        if alpha <= 0.01:
            return
        a = int(255 * R.clamp(alpha))
        ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        tips = [(p1[0] - head * math.cos(ang + s * 0.5),
                 p1[1] - head * math.sin(ang + s * 0.5)) for s in (-1, 1)]
        xs = [p0[0], p1[0]] + [t[0] for t in tips]
        ys = [p0[1], p1[1]] + [t[1] for t in tips]
        layer, ox, oy = _layer((min(xs), min(ys), max(xs), max(ys)), pad=width + 2)
        d = ImageDraw.Draw(layer)
        d.line([(p0[0] - ox, p0[1] - oy), (p1[0] - ox, p1[1] - oy)],
               fill=color + (a,), width=width)
        for t in tips:
            d.line([(p1[0] - ox, p1[1] - oy), (t[0] - ox, t[1] - oy)],
                   fill=color + (a,), width=width)
        _blend(img, layer, ox, oy)

    @staticmethod
    def ring(img, cx, cy, r, color, alpha, width=6):
        if alpha <= 0.01 or r <= 0:
            return
        layer, ox, oy = _layer((cx - r, cy - r, cx + r, cy + r), pad=width + 2)
        d = ImageDraw.Draw(layer)
        d.ellipse((cx - r - ox, cy - r - oy, cx + r - ox, cy + r - oy),
                  outline=color + (int(255 * R.clamp(alpha)),), width=width)
        _blend(img, layer, ox, oy)

    @staticmethod
    def press_events(events, t, decay=0.34):
        """events: [(idx, t0)] -> {idx: 0..1}"""
        out = {}
        for idx, t0 in events:
            if t >= t0:
                p = max(0.0, 1.0 - (t - t0) / decay)
                out[idx] = max(out.get(idx, 0.0), p)
        return out

    @staticmethod
    def progress_bar(img, ratio, y=1868):
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((90, y, R.W - 90, y + 8), radius=4, fill=(255, 255, 255, 28))
        d.rounded_rectangle((90, y, 90 + (R.W - 180) * R.clamp(ratio), y + 8),
                            radius=4, fill=R.ACCENT)

    # ------------------------------------------------------------ 爆款包装（大字动效）

    @staticmethod
    def _fade(layer, a):
        """把 RGBA 小图层的整体不透明度乘 a（C 级运算，不用逐像素 Python 回调）。"""
        a = R.clamp(a)
        if a >= 0.999:
            return layer
        if a <= 0.001:
            return None
        alpha = ImageChops.multiply(layer.getchannel("A"),
                                    Image.new("L", layer.size, int(255 * a)))
        out = layer.copy()
        out.putalpha(alpha)
        return out

    @staticmethod
    def kinetic_text(img, xy, s, size, fill=R.TEXT, t=0.0, dur=0.55, style="pop",
                     bold=True, anchor="mm", alpha=1.0, stroke=None, stroke_w=None,
                     shadow=0.0):
        """大字入场动效（封面/钩子镜头用）。t 为镜头内秒数。

        style: pop=回弹放大 / slam=砸下 / rise=上浮淡入 / wipe=从左揭幕
        stroke + stroke_w: 描边色与宽度（描边是"网红大字"的关键，浅底必配深描边）
        shadow: 0~1，向下投影强度
        """
        if not s or alpha <= 0.01:
            return
        s = R.sanitize(s, bold=bold)
        p = R.clamp(t / dur) if dur > 0 else 1.0
        scale, dy, a = 1.0, 0.0, R.clamp(alpha)
        if style == "pop":
            scale = 0.55 + 0.45 * R.ease_back(p, 2.2)
            a *= R.clamp(p * 2.2)
        elif style == "slam":
            scale = 1.75 - 0.75 * R.ease_out(p)
            dy = -60 * (1 - R.ease_out(p))
            a *= R.clamp(p * 3.0)
        elif style == "rise":
            dy = 46 * (1 - R.ease_out(p))
            a *= R.clamp(p * 1.6)
        elif style == "wipe":
            a *= R.clamp(p * 1.4)

        fsize = max(8, int(round(size * scale)))
        f = R.font(fsize, bold=bold)
        probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        bx = probe.textbbox((0, 0), s, font=f, anchor=anchor,
                            stroke_width=stroke_w or 0)
        pad = (stroke_w or 0) + 4
        layer, ox, oy = _layer((xy[0] + bx[0], xy[1] + bx[1] + dy,
                                xy[0] + bx[2], xy[1] + bx[3] + dy), pad=pad)
        d = ImageDraw.Draw(layer)
        lx, ly = xy[0] - ox, xy[1] + dy - oy
        if shadow > 0:
            d.text((lx, ly + max(2, fsize // 22)), s, font=f, anchor=anchor,
                   fill=(0, 0, 0, int(150 * shadow)),
                   stroke_width=(stroke_w or 0) + 2, stroke_fill=(0, 0, 0, int(120 * shadow)))
        d.text((lx, ly), s, font=f, anchor=anchor, fill=tuple(fill) + (255,),
               stroke_width=stroke_w or 0,
               stroke_fill=(tuple(stroke) + (255,)) if stroke else None)
        if style == "wipe":  # 从左向右揭开：裁掉右侧未揭开的部分
            keep = int(layer.size[0] * R.ease_out(p))
            if keep < layer.size[0]:
                layer = layer.crop((0, 0, max(1, keep), layer.size[1]))
        layer = SceneAPI._fade(layer, a)
        if layer is not None:
            _blend(img, layer, ox, oy)

    @staticmethod
    def highlight_bar(img, x0, x1, y, h, p=1.0, color=None, radius=None, alpha=0.85):
        """关键词底纹色块（自左向右扫过），用来压住一行字的重点词。"""
        p = R.clamp(p)
        if p <= 0.01 or x1 <= x0:
            return
        color = tuple(color or R.AMBER)
        w = (x1 - x0) * R.ease_out(p)
        layer, ox, oy = _layer((x0, y, x0 + w, y + h), pad=1)
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((x0 - ox, y - oy, x0 + w - ox, y + h - oy),
                            radius=int(radius if radius is not None else h / 2),
                            fill=color + (int(255 * R.clamp(alpha)),))
        _blend(img, layer, ox, oy)

    @staticmethod
    def number_badge(img, cx, cy, n, r=46, color=None, num_color=(12, 16, 28),
                     alpha=1.0, font_ratio=0.62):
        """圆形序号贴纸（① ② ③ 的图形版，比文字序号更醒目）。"""
        if alpha <= 0.01:
            return
        color = tuple(color or R.ACCENT)
        layer, ox, oy = _layer((cx - r, cy - r, cx + r, cy + r), pad=2)
        d = ImageDraw.Draw(layer)
        d.ellipse((cx - r - ox, cy - r - oy, cx + r - ox, cy + r - oy),
                  fill=color + (int(255 * R.clamp(alpha)),))
        d.text((cx - ox, cy - oy), str(n), font=R.font(int(r * 2 * font_ratio), bold=True),
               fill=tuple(num_color) + (int(255 * R.clamp(alpha)),), anchor="mm")
        _blend(img, layer, ox, oy)

    @staticmethod
    def panel(img, box, fill=(10, 14, 26), alpha=0.72, radius=32, outline=None,
              outline_w=2, pad=2):
        """圆角半透明面板（放结论/公式/对比，把信息"框"起来）。"""
        if alpha <= 0.01:
            return
        x0, y0, x1, y1 = box
        layer, ox, oy = _layer(box, pad=pad)
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((x0 - ox, y0 - oy, x1 - ox, y1 - oy), radius=radius,
                            fill=tuple(fill) + (int(255 * R.clamp(alpha)),),
                            outline=(tuple(outline) + (int(200 * R.clamp(alpha)),))
                            if outline else None, width=outline_w)
        _blend(img, layer, ox, oy)

    # ------------------------------------------------------------ 常驻装饰（提升完播 / 合规）

    @staticmethod
    def seg_dots(img, idx, n, y=96, r=9, gap=26, color=None):
        """顶部分段进度点：让观众知道"还有几段"，是完播率的常见小手段。"""
        if n <= 0:
            return
        color = tuple(color or R.ACCENT)
        total = (n - 1) * gap
        x0 = R.W / 2 - total / 2
        d = ImageDraw.Draw(img, "RGBA")
        for i in range(n):
            cx = x0 + i * gap
            if i < idx:
                d.ellipse((cx - r * 0.7, y - r * 0.7, cx + r * 0.7, y + r * 0.7),
                          fill=color + (150,))
            elif i == idx:
                d.ellipse((cx - r, y - r, cx + r, y + r), fill=color + (255,))
            else:
                d.ellipse((cx - r * 0.7, y - r * 0.7, cx + r * 0.7, y + r * 0.7),
                          fill=(255, 255, 255, 60))

    @staticmethod
    def disclosure(img, text="AI 配音", alpha=0.5, y=None, x=None):
        """AI 合成语音标识（平台合规要求：合成人声需显著标识）。"""
        if not text:
            return
        y = R.H - 104 if y is None else y     # 进度条在 1868，标识放它上方不打架
        x = R.W - 40 if x is None else x
        f = R.font(24, bold=False)
        tw = f.getlength(text)
        layer, ox, oy = _layer((x - tw - 24, y - 20, x, y + 20), pad=2)
        d = ImageDraw.Draw(layer)
        d.rounded_rectangle((x - tw - 24 - ox, y - 20 - oy, x - ox, y + 20 - oy),
                            radius=20, fill=(255, 255, 255, int(26 * 255 * alpha)),
                            outline=(255, 255, 255, int(60 * 255 * alpha)), width=1)
        d.text((x - 12 - ox, y - oy), text, font=f,
               fill=(255, 255, 255, int(210 * alpha)), anchor="rm")
        _blend(img, layer, ox, oy)

    # ------------------------------------------------------------ 片头（0.6~0.9s 的品牌开场）

    @staticmethod
    def intro(img, t, title="", sub="", dur=1.7, style="bar"):
        """片头：默认是**顶部栏目条**（不挡钩子画面），需要时可用 takeover 全屏版。

        为什么默认不用全屏：前 3 秒是完播率的生死线，全屏片头会白白吃掉 1/4 的黄金时间。
        顶部条从左侧滑入、停留、滑出，既打了账号签名，又不影响钩子大字第一帧就出现。
        """
        if not title or dur <= 0 or t < 0 or t > dur:
            return
        if style == "takeover":
            SceneAPI._intro_takeover(img, t, title, sub, dur)
            return
        p = R.clamp(t / dur)
        # 0~0.22 滑入，0.22~0.72 停留，0.72~1 滑出
        if p < 0.22:
            k = R.ease_out(p / 0.22)
        elif p > 0.72:
            k = 1 - R.ease_in_out((p - 0.72) / 0.28)
        else:
            k = 1.0
        if k <= 0.01:
            return
        f = R.font(34, bold=True)
        tw = f.getlength(title)
        bar_w = tw + 64
        bar_h = 62
        x = int(-bar_w + (bar_w + 44) * k)      # 从左侧滑到 x=44
        y = 128
        SceneAPI.panel(img, (x, y, x + bar_w, y + bar_h), fill=R.ACCENT,
                       alpha=0.95, radius=bar_h // 2)
        ImageDraw.Draw(img, "RGBA").text((x + 32, y + bar_h / 2), title, font=f,
                                         fill=(10, 14, 26, 255), anchor="lm")
        if sub and k > 0.6:
            SceneAPI.text(img, (x + bar_w + 20, y + bar_h / 2), sub, 30, fill=R.DIM,
                          bold=False, anchor="lm", alpha=(k - 0.6) / 0.4)

    @staticmethod
    def _intro_takeover(img, t, title, sub, dur):
        """全屏片头（更"大片"但会挡画面，慎用）。"""
        if dur <= 0 or t < 0 or t > dur:
            return
        p = R.clamp(t / dur)
        fade = 1.0 if p < 0.58 else R.clamp(1 - (p - 0.58) / 0.42)
        if fade <= 0.01:
            return
        lift = -int(R.ease_in_out(R.clamp((p - 0.58) / 0.42)) * 90)
        cy = int(R.H * 0.44) + lift
        # 压暗底
        layer, ox, oy = _layer((0, 0, R.W, R.H), pad=0)
        ImageDraw.Draw(layer).rectangle((0, 0, R.W, R.H),
                                        fill=(6, 9, 16, int(246 * fade)))
        _blend(img, layer, ox, oy)
        # 片头名下垫实底，避免与钩子大字重叠
        SceneAPI.panel(img, (R.W / 2 - 430, cy - 75, R.W / 2 + 430, cy + 75),
                       fill=R.BG_TOP, alpha=0.98 * fade, radius=26)
        # 强调线扫开
        grow = R.ease_out(R.clamp(p / 0.32))
        half = int(R.W * 0.34 * grow)
        if half > 2:
            ImageDraw.Draw(img, "RGBA").rectangle(
                (R.W / 2 - half, cy + 74, R.W / 2 + half, cy + 84),
                fill=tuple(R.ACCENT) + (int(235 * fade),))
        if title:
            scale = 1.18 - 0.18 * R.ease_out(R.clamp(p / 0.34))
            SceneAPI.kinetic_text(img, (R.W / 2, cy), title, int(92 * scale), fill=R.TEXT,
                                  t=t - 0.04, dur=0.3, style="slam",
                                  stroke=R.BG_BOT, stroke_w=5, shadow=0.85, alpha=fade)
        if sub:
            a = R.clamp((p - 0.26) / 0.2) * fade
            SceneAPI.text(img, (R.W / 2, cy + 132), sub, 40, fill=R.ACCENT,
                          bold=True, anchor="mm", alpha=a)

