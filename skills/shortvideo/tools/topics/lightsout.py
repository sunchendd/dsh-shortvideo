"""内置题材包：九宫格熄灯 —— 从奇偶抵消到矩阵求逆。

数学内核：GF(2) 上构造矩阵 A、高斯-约当求逆、验证四角解。
（移植自 Windows 侧 lightsout-video 项目，已验证 A 可逆、解唯一。）
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from shortvideo.runtime import ease_out, ease_in_out, ease_back, seg, clamp, lerp
from shortvideo.runtime import ACCENT, AMBER, GREEN, TILE_ON, DIM, TEXT


# ---------------------------------------------------------------- GF(2) 内核

def build_A(n=3):
    m = n * n
    A = [[0] * m for _ in range(m)]
    for r in range(n):
        for c in range(n):
            i = r * n + c
            for dr, dc in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
                rr, cc = r + dr, c + dc
                if 0 <= rr < n and 0 <= cc < n:
                    A[i][rr * n + cc] = 1
    return A


def inverse(A):
    m = len(A)
    M = [A[i][:] + [1 if i == j else 0 for j in range(m)] for i in range(m)]
    for c in range(m):
        pivot = next((r for r in range(c, m) if M[r][c]), None)
        if pivot is None:
            return None
        M[c], M[pivot] = M[pivot], M[c]
        for r in range(m):
            if r != c and M[r][c]:
                M[r] = [a ^ b for a, b in zip(M[r], M[c])]
    return [row[m:] for row in M]


def mat_vec(M, v):
    return [sum(a * b for a, b in zip(row, v)) % 2 for row in M]


def press(lights, idx, n=3):
    out = lights[:]
    r, c = divmod(idx, n)
    for dr, dc in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
        rr, cc = r + dr, c + dc
        if 0 <= rr < n and 0 <= cc < n:
            j = rr * n + cc
            out[j] ^= 1
    return out


# ---------------------------------------------------------------- ctx

def prepare(ctx):
    n = 3
    A = build_A(n)
    Ai = inverse(A)
    demo_b = [0] * 9
    for i in (0, 2, 6, 8):
        demo_b[i] = 1
    demo_x = mat_vec(Ai, demo_b)
    assert mat_vec(A, demo_x) == demo_b, "A x != b?!"
    ctx.update(n=n, A=A, Ai=Ai, demo_b=demo_b, demo_x=demo_x,
               a_col5=[A[r][4] for r in range(9)])
    return ctx


# ---------------------------------------------------------------- 镜头

def shot_hook(img, t, dur, ctx, S):
    import math
    a1 = ease_out(seg(t, 0.10, 0.75))
    S.text(img, (540, 430 - 30 * (1 - a1)), "烧脑的来了", 56, TILE_ON, bold=True, alpha=a1)
    a2 = seg(t, 0.4, 1.05)
    a2b = 1 + 1.6 * (a2 - 1) ** 3 + 1.6 * (a2 - 1) ** 2
    S.text(img, (540, 560), "九宫格拆弹", int(126 * clamp(a2b)), TEXT, bold=True, alpha=clamp(a2b))
    a3 = ease_out(seg(t, 0.85, 1.45))
    S.text(img, (540, 690), "从奇偶抵消到矩阵求逆", 52, ACCENT, alpha=a3)
    sweep = (t - 1.1) * 1.15
    lights = []
    for i in range(9):
        v = clamp((sweep - i * 0.055) / 0.5)
        wave = 0.55 + 0.45 * math.sin(t * 1.6 - i * 0.5)
        lights.append(v * wave)
    S.grid(img, lights, top=880)
    a4 = ease_out(seg(t, 2.0, 2.6))
    S.tag(img, 540, 1552, "9 格 · 每格 按 / 不按", AMBER, a4, 38)


def shot_rule(img, t, dur, ctx, S):
    S.title(img, "① 一次点击的影响", "按一格，翻五格", ease_out(seg(t, 0.05, 0.55)))
    t_press = 1.30
    cross = [1, 3, 4, 5, 7]
    lights = [0.0] * 9
    if t >= t_press:
        for k, i in enumerate(cross):
            lights[i] = ease_out(clamp((t - (t_press + k * 0.09)) / 0.22))
    press = S.press_events([(4, t_press - 0.18)], t)
    S.grid(img, lights, top=640, press=press)
    tt = t - t_press
    if 0 <= tt < 0.9:
        S.ring(img, 540, 838, 40 + 320 * ease_out(tt / 0.9), ACCENT, 0.8 * (1 - tt / 0.9), 7)
    a = ease_out(seg(t, t_press + 0.55, t_press + 1.05))
    S.text(img, (540, 1360), "自己 + 上下左右 = 一个十字", 48, AMBER, alpha=a)


def shot_insight(img, t, dur, ctx, S):
    S.title(img, "② 奇偶抵消", "按两次 = 没按", ease_out(seg(t, 0.05, 0.55)))
    base = [1, 0, 1, 0, 0, 0, 1, 0, 1]
    t1, t2 = 1.15, 2.05

    def board_at(tt):
        b = base[:]
        if tt >= t1:
            b = press(b, 4)
        if tt >= t2:
            b = press(b, 4)
        return b

    prev = board_at(max(0.0, t - 0.16))
    now = board_at(t)
    fr = clamp((t - max(0.0, t - 0.16)) / 0.16)
    lights = [lerp(prev[i], now[i], fr) for i in range(9)]
    pe = S.press_events([(4, t1 - 0.16), (4, t2 - 0.16)], t)
    S.grid(img, lights, top=620, size=168, gap=22, press=pe)
    if t < t2:
        a = ease_out(seg(t, t1 - 0.1, t1 + 0.2))
        S.tag(img, 540, 1310, "第 1 次：翻转", TILE_ON, a, 38)
    else:
        a2 = ease_out(seg(t, t2, t2 + 0.25))
        S.tag(img, 540, 1310, "第 2 次：全部复原", GREEN, a2, 38)
    a3 = ease_out(seg(t, t2 + 0.45, t2 + 0.95))
    S.formula(img, 540, 1420, [("同一个格子按两次", 46, DIM, False, 0),
                               ("  ≡ 0", 46, GREEN, True, 0)], alpha=a3)
    a4 = ease_out(seg(t, t2 + 1.0, t2 + 1.5))
    S.formula(img, 540, 1510, [("x", 52, ACCENT, True, 0), ("j", 32, ACCENT, True, 12),
                               (" ∈ {0, 1}　加法取模 2　顺序无关", 44, TEXT, False, 0)], alpha=a4)


def shot_reveal(img, t, dur, ctx, S):
    S.title(img, "③ 矩阵求逆", "解是唯一的", ease_out(seg(t, 0.05, 0.55)))
    A, Ai = ctx["A"], ctx["Ai"]
    fade = ease_in_out(seg(t, 1.35, 2.25))
    if fade < 1.0:
        S.matrix(img, A, 9, 540, 760, cell=56, gap=8, alpha=1 - fade,
                 label="A" if fade < 0.5 else None)
    if fade > 0:
        S.matrix(img, Ai, 9, 540, 760, cell=56, gap=8, alpha=fade,
                 hi_color=ACCENT, label="A 的逆" if fade > 0.5 else None)
    a = ease_out(seg(t, 2.3, 2.85))
    S.formula(img, 540, 1190, [("x = ", 70, TEXT, True, 0),
                               ("A", 48, ACCENT, True, 0), ("-1", 34, ACCENT, True, -26),
                               (" b", 70, TEXT, True, 0)], alpha=a)
    a2 = ease_out(seg(t, 2.75, 3.3))
    S.text(img, (540, 1330), "不用搜索 · 不用试错 · 一次乘法出答案", 44, GREEN, alpha=a2)


def shot_solve(img, t, dur, ctx, S):
    S.title(img, "④ 实战拆弹", "四角亮？按四角", ease_out(seg(t, 0.05, 0.55)))
    b0, x = ctx["demo_b"], ctx["demo_x"]
    presses = [i for i, v in enumerate(x) if v]
    t_start, step = 1.05, 0.55

    def board_at(tt):
        bb = b0[:]
        for k, i in enumerate(presses):
            if tt >= t_start + k * step:
                bb = press(bb, i)
        return bb

    prev = board_at(max(0.0, t - 0.14))
    now = board_at(t)
    fr = clamp((t - max(0.0, t - 0.14)) / 0.14)
    lights = [lerp(prev[i], now[i], fr) for i in range(9)]
    events = [(i, t_start + k * step) for k, i in enumerate(presses)]
    rects = S.grid(img, lights, top=620, size=132, gap=16,
                   press=S.press_events(events, t),
                   labels=[str(i + 1) for i in range(9)])
    a = ease_out(seg(t, 0.55, 1.0))
    xs = "[" + ", ".join(str(v) for v in x) + "]"
    S.formula(img, 540, 1265, [("x = ", 44, DIM, True, 0),
                               ("A", 44, ACCENT, True, 0), ("-1", 26, ACCENT, True, -15),
                               (" b = ", 44, DIM, True, 0), (xs, 44, ACCENT, True, 0)], alpha=a)
    done_t = t_start + len(presses) * step
    if t >= done_t - 0.05:
        a2 = ease_out(seg(t, done_t, done_t + 0.5))
        S.text(img, (540, 1385), "全部熄灭", 56, GREEN, bold=True, alpha=a2)
        a3 = ease_out(seg(t, done_t + 0.35, done_t + 0.9))
        S.text(img, (540, 1480), "算一次逆矩阵，之后所有局面都是查表", 42, AMBER, alpha=a3)


def build(ctx):
    return {
        "hook": shot_hook,
        "rule": shot_rule,
        "insight": shot_insight,
        "reveal": shot_reveal,
        "solve": shot_solve,
    }
