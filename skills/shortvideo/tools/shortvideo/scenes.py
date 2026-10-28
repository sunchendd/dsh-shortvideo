"""SceneAPI：题材包可用的全部绘图基元。

约定：所有方法第一个参数是当前帧画布（RGBA，1080x1920），
时间参数 t 为镜头内本地秒数。题材包永远通过 SceneAPI 画图。
"""
import math

from PIL import Image, ImageDraw

from . import runtime as R


class SceneAPI:
    # ------------------------------------------------------------ 文本

    @staticmethod
    def text(img, xy, s, size, fill=R.TEXT, bold=False, math=False, anchor="mm", alpha=1.0):
        if alpha <= 0.01:
            return
        d = ImageDraw.Draw(img, "RGBA")
        d.text(xy, s, font=R.font(size, bold, math),
               fill=tuple(fill) + (int(255 * R.clamp(alpha)),), anchor=anchor)

    @staticmethod
    def title(img, kicker, main, alpha=1.0, y=300):
        SceneAPI.text(img, (R.W / 2, y), kicker, 36, R.ACCENT, bold=True, alpha=alpha * 0.9)
        if main:
            SceneAPI.text(img, (R.W / 2, y + 74), main, 62, R.TEXT, bold=True, alpha=alpha)

    @staticmethod
    def wrap(s, size, max_w, bold=False):
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
        return lines

    @staticmethod
    def formula(img, cx, cy, parts, alpha=1.0):
        """parts: [(文本, 字号, 颜色, 是否数学体, dy)]，居中拼接，dy 负值=上标。"""
        d = ImageDraw.Draw(img, "RGBA")
        widths = [R.font(size, math=m).getlength(s) for s, size, _c, m, _d in parts]
        x = cx - sum(widths) / 2
        a = int(255 * R.clamp(alpha))
        for (s, size, color, m, dy), w in zip(parts, widths):
            d.text((x, cy + dy), s, font=R.font(size, math=m),
                   fill=tuple(color) + (a,), anchor="lm")
            x += w

    @staticmethod
    def caption(img, s, alpha=1.0, y=1645):
        if not s or alpha <= 0.01:
            return
        size = 50
        lines = SceneAPI.wrap(s, size, 880, bold=True)
        line_h = int(size * 1.42)
        h = line_h * len(lines) + 44
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        a = R.clamp(alpha)
        d.rounded_rectangle(((R.W - 940) / 2, y - 22, (R.W + 940) / 2, y - 22 + h),
                            radius=28, fill=(8, 12, 24, int(190 * a)),
                            outline=R.ACCENT + (int(60 * a),), width=2)
        for i, ln in enumerate(lines):
            d.text((R.W / 2, y + 10 + line_h * i + line_h / 2), ln,
                   font=R.font(size, bold=True), fill=R.TEXT + (int(255 * a),), anchor="mm")
        img.alpha_composite(layer)

    @staticmethod
    def tag(img, cx, cy, s, color=R.ACCENT, alpha=1.0, size=34):
        if alpha <= 0.01:
            return
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        f = R.font(size, bold=True)
        w = f.getlength(s) + 46
        a = R.clamp(alpha)
        d.rounded_rectangle((cx - w / 2, cy - size * 1.05, cx + w / 2, cy + size * 1.05),
                            radius=int(size * 1.05), fill=color + (int(38 * a),),
                            outline=color + (int(150 * a),), width=2)
        d.text((cx, cy), s, font=f, fill=color + (int(255 * a),), anchor="mm")
        img.alpha_composite(layer)

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
                d.text((cx, cy), labels[i], font=R.font(int(size * 0.34), bold=True),
                       fill=(255, 255, 255) if lit > 0.5 else R.FAINT, anchor="mm")
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
                d.text((x + cell / 2, y + cell / 2), str(v),
                       font=R.font(int(cell * 0.58), bold=bool(v), math=True),
                       fill=((236, 242, 255) if v else (118, 136, 168)) + (int(a * local),),
                       anchor="mm")
        if label:
            d.text((cx, y0 - 34), label, font=R.font(34, bold=True),
                   fill=R.DIM + (a,), anchor="mm")
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
                d.text((cx - max_w / 2 + 18, y + bar_h / 2), labels[i],
                       font=R.font(int(bar_h * 0.5), bold=True),
                       fill=(10, 14, 26, int(255 * a)), anchor="lm")
            rects.append(rect)
            y += bar_h + gap
        return rects

    @staticmethod
    def arrow(img, p0, p1, color=R.ACCENT, width=5, alpha=1.0, head=18):
        if alpha <= 0.01:
            return
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        a = int(255 * R.clamp(alpha))
        d.line([p0, p1], fill=color + (a,), width=width)
        ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
        for s in (-1, 1):
            d.line([p1, (p1[0] - head * math.cos(ang + s * 0.5),
                         p1[1] - head * math.sin(ang + s * 0.5))],
                   fill=color + (a,), width=width)
        img.alpha_composite(layer)

    @staticmethod
    def ring(img, cx, cy, r, color, alpha, width=6):
        if alpha <= 0.01 or r <= 0:
            return
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        d.ellipse((cx - r, cy - r, cx + r, cy + r),
                  outline=color + (int(255 * R.clamp(alpha)),), width=width)
        img.alpha_composite(layer)

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
