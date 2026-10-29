"""内置题材包：排序算法（冒泡 vs 选择，为何 O(n²) 差在哪）。

数学内核：sort_trace() 真实跑排序并记录每步数组状态，画面只消费 trace。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from shortvideo.runtime import ease_out, ease_in_out, seg, clamp, lerp
from shortvideo.runtime import ACCENT, AMBER, GREEN, TILE_ON, DIM, TEXT


# ---------------------------------------------------------------- 数学内核

def bubble_trace(arr):
    """返回 [(数组快照, 本轮比较对i, 是否发生交换)]。"""
    a = arr[:]
    out = []
    n = len(a)
    for i in range(n):
        for j in range(n - 1 - i):
            swapped = a[j] > a[j + 1]
            out.append((a[:], (j, j + 1), swapped))
            if swapped:
                a[j], a[j + 1] = a[j + 1], a[j]
                out.append((a[:], (j, j + 1), True))
    out.append((a[:], None, False))
    return out


def selection_trace(arr):
    a = arr[:]
    out = []
    n = len(a)
    for i in range(n):
        m = i
        for j in range(i + 1, n):
            out.append((a[:], (j, m), False, i))
            if a[j] < a[m]:
                m = j
        a[i], a[m] = a[m], a[i]
        out.append((a[:], (m, i), True, i))
    out.append((a[:], None, False, n))
    return out


DEMO = [5, 2, 8, 1, 9, 3]


def prepare(ctx):
    ctx["demo"] = DEMO
    ctx["bubble"] = bubble_trace(DEMO)
    ctx["sel"] = selection_trace(DEMO)
    ctx["n"] = len(DEMO)
    return ctx


# ---------------------------------------------------------------- 绘制

def _bars(img, snapshot, compare=None, swapped=False, baseline=1150, bar_w=118, gap=22,
          labels=True, height_k=58, S=None):
    """baseline = 柱子底边 y；height_k = 每单位数值对应像素高度（9 -> 522px）。"""
    n = len(snapshot)
    total_w = n * bar_w + (n - 1) * gap
    x0 = (1080 - total_w) / 2
    for i, v in enumerate(snapshot):
        x = x0 + i * (bar_w + gap)
        bh = v * height_k
        y1 = baseline
        y0 = y1 - bh
        hot = compare is not None and i in compare
        color = (255, 82, 116) if hot else ((255, 181, 71) if swapped else (61, 232, 255))
        S.tag(img, (x + bar_w / 2), y0 - 34, str(v), color, 1.0, size=30)
        import PIL.ImageDraw as _ID
        d = _ID.Draw(img, "RGBA")
        d.rounded_rectangle((x, y0, x + bar_w, y1), radius=14, fill=color + (235,),
                            outline=color + (255,), width=2)


def shot_hook(img, t, dur, ctx, S):
    a1 = ease_out(seg(t, 0.10, 0.75))
    S.text(img, (540, 430 - 30 * (1 - a1)), "烧脑的来了", 56, TILE_ON, bold=True, alpha=a1)
    a2 = seg(t, 0.4, 1.05)
    a2b = 1 + 1.6 * (a2 - 1) ** 3 + 1.6 * (a2 - 1) ** 2  # ease_back
    S.text(img, (540, 560), "六个数，两种排法", int(116 * clamp(a2b)), TEXT, bold=True,
           alpha=clamp(a2b))
    a3 = ease_out(seg(t, 0.85, 1.45))
    S.text(img, (540, 690), "为什么一个快，一个慢一倍？", 52, ACCENT, alpha=a3)
    _bars(img, DEMO, baseline=1300, S=S)
    a4 = ease_out(seg(t, 2.0, 2.6))
    S.tag(img, 540, 1560, "冒泡 vs 选择 · 都是 O(n²)", AMBER, a4, 38)


def shot_rule(img, t, dur, ctx, S):
    S.title(img, "① 排序的代价", "比较，才有信息", ease_out(seg(t, 0.05, 0.55)))
    trace = ctx["bubble"]
    # 播放冒泡前 6 步
    idx = min(int((t - 0.8) * 2.2), len(trace) - 1)
    idx = max(0, idx)
    snap, cmp_, sw = trace[idx]
    _bars(img, snap, compare=cmp_, swapped=sw, baseline=1150, S=S)
    a = ease_out(seg(t, 1.2, 1.7))
    S.text(img, (540, 1360), "每比一次，只知道这两个数的大小关系", 42, AMBER, alpha=a)
    a2 = ease_out(seg(t, 2.0, 2.5))
    S.formula(img, 540, 1460, [
        ("比较次数 ≈ n(n-1)/2", 48, TEXT, True, 0)], alpha=a2)


def shot_insight(img, t, dur, ctx, S):
    S.title(img, "② 冒泡的浪费", "每一步都可能白比", ease_out(seg(t, 0.05, 0.55)))
    trace = ctx["bubble"]
    idx = min(int((t - 0.8) * 2.6), len(trace) - 1)
    snap, cmp_, sw = trace[max(0, idx)]
    _bars(img, snap, compare=cmp_, swapped=sw, baseline=1150, S=S)
    a = ease_out(seg(t, 1.4, 1.9))
    S.text(img, (540, 1360), "相邻才比 → 大数一步步\"冒\"到末尾", 42, AMBER, alpha=a)
    a2 = ease_out(seg(t, 2.2, 2.7))
    S.text(img, (540, 1460), "交换发生在每一轮的每一处", 42, DIM, alpha=a2)


def shot_reveal(img, t, dur, ctx, S):
    S.title(img, "③ 选择的聪明", "一轮只换一次", ease_out(seg(t, 0.05, 0.55)))
    trace = ctx["sel"]
    idx = min(int((t - 0.8) * 2.6), len(trace) - 1)
    item = trace[max(0, idx)]
    snap, cmp_, sw, boundary = item
    _bars(img, snap, compare=cmp_ if cmp_ else None, swapped=sw, baseline=1150, S=S)
    a = ease_out(seg(t, 1.4, 1.9))
    S.text(img, (540, 1360), "扫完一轮记住最小值，只交换一次", 42, GREEN, alpha=a)
    a2 = ease_out(seg(t, 2.2, 2.7))
    S.formula(img, 540, 1460, [
        ("交换次数 ≤ n-1", 48, TEXT, True, 0)], alpha=a2)


def shot_solve(img, t, dur, ctx, S):
    S.title(img, "④ 对决结果", "同一个数组", ease_out(seg(t, 0.05, 0.55)))
    b, s = ctx["bubble"], ctx["sel"]
    grow = ease_out(seg(t, 0.7, 2.2))
    S.text(img, (540, 560), "冒泡", 44, ACCENT, bold=True, alpha=grow)
    S.bar_chart(img, [clamp(len(b) / 90)], 540, 700, max_w=760, bar_h=64,
                labels=[f"{len(b)} 步"], grow=grow, alpha=grow)
    S.text(img, (540, 900), "选择", 44, GREEN, bold=True, alpha=grow)
    S.bar_chart(img, [clamp(len(s) / 90)], 540, 1040, max_w=760, bar_h=64,
                labels=[f"{len(s)} 步"], grow=grow, alpha=grow)
    a = ease_out(seg(t, 2.4, 3.0))
    S.text(img, (540, 1330), "比较次数一样，但选择排序少一半交换", 42, AMBER, alpha=a)
    a2 = ease_out(seg(t, 3.1, 3.6))
    S.text(img, (540, 1430), "数据越大，差距越大 —— 这就是算法设计的意义", 40, TEXT, alpha=a2)


def build(ctx):
    return {
        "hook": shot_hook,
        "rule": shot_rule,
        "insight": shot_insight,
        "reveal": shot_reveal,
        "solve": shot_solve,
    }
