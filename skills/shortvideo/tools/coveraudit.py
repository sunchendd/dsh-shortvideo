"""封面一致性体检：把一批片子的一版封面拼成一张"账号视觉体检表"，顺带检查字段齐不齐。

为什么需要：单条封面好看不等于"像同一个账号"。观众刷到第 3 条才认得出你，
靠的是固定的栏目位置、同一套强调色、同样的标题行数——这些只能横向对比才看得出来。

用法：
  python coveraudit.py --dir examples [--variant f1] [--out covers.png] [--cols 3]
"""
import argparse
import glob
import json
import os
import sys

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shortvideo import runtime as R  # noqa: E402

NEED = [("kicker", "栏目名"), ("headline", "主标题"), ("highlight", "高亮词"),
        ("sub", "副标题"), ("tags", "话题标签"), ("cta", "行动号召")]


def load(dirpath):
    items = []
    for f in sorted(glob.glob(os.path.join(dirpath, "*.content.json"))):
        try:
            cfg = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        slug = os.path.basename(f).replace(".content.json", "")
        cov = cfg.get("cover") or {}
        items.append({"slug": slug, "cover": cov,
                      "img": os.path.join(dirpath, "out", slug, "cover_f1.png")})
    return items


def audit(items):
    """字段齐不齐 + 视觉参数收不收敛。"""
    problems, rows = [], []
    line_counts, tag_counts, kicks = [], [], []
    for it in items:
        cov = it["cover"]
        missing = [label for key, label in NEED if not cov.get(key)]
        if missing:
            problems.append(f"{it['slug']}：封面缺 {', '.join(missing)}")
        head = cov.get("headline") or []
        line_counts.append(len(head))
        tag_counts.append(len(cov.get("tags") or []))
        kicks.append(str(cov.get("kicker") or ""))
        if not it.get("img") or not os.path.exists(it["img"]):
            problems.append(f"{it['slug']}：没有 cover_f1.png（还没出片？）")
        rows.append((it["slug"], str(cov.get("kicker") or "-"), len(head),
                     len(cov.get("tags") or []), str(cov.get("cta") or "-")))
    if line_counts and max(line_counts) - min(line_counts) > 1:
        problems.append(f"主标题行数不统一：{sorted(set(line_counts))}（同一栏目建议固定 2~3 行）")
    if kicks and len(set(k for k in kicks if k)) < len(kicks):
        dup = [k for k in set(kicks) if kicks.count(k) > 1 and k]
        if dup:
            problems.append(f"栏目名重复：{dup}（同栏目是好习惯，但确认是有意的）")
    return rows, problems


def sheet(items, out_path, variant="f1", cols=3, cell_w=360):
    imgs = [(it["slug"], Image.open(it["img"]).convert("RGB"))
            for it in items if os.path.exists(it.get("img") or "") and variant in it["img"]]
    if not imgs:
        return None
    cell_h = int(cell_w * 1440 / 1080)
    rows = (len(imgs) + cols - 1) // cols
    pad = 14
    W = cols * cell_w + (cols + 1) * pad
    H = rows * (cell_h + 34) + (rows + 1) * pad
    canvas = Image.new("RGB", (W, H), (10, 13, 20))
    d = ImageDraw.Draw(canvas)
    f = R.font(20, bold=True)
    for i, (slug, im) in enumerate(imgs):
        r, c = divmod(i, cols)
        x = pad + c * (cell_w + pad)
        y = pad + r * (cell_h + 34 + pad)
        canvas.paste(im.resize((cell_w, cell_h), Image.LANCZOS), (x, y))
        d.text((x + 4, y + cell_h + 6), slug, font=f, fill=(170, 185, 210))
    canvas.save(out_path)
    return out_path


def main():
    ap = argparse.ArgumentParser(description="封面一致性体检（拼图 + 字段检查）")
    ap.add_argument("--dir", default="examples")
    ap.add_argument("--variant", default="f1")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cols", type=int, default=3)
    args = ap.parse_args()
    items = load(args.dir)
    if not items:
        print(f"[coveraudit] {args.dir} 下没有 *.content.json")
        return
    rows, problems = audit(items)
    print(f"\n■ 封面一致性体检（{len(items)} 条）\n")
    print(f"  {'条目':<24}{'栏目':<14}{'标题行':>6}{'标签':>6}  行动号召")
    for slug, kick, lines, tags, cta in rows:
        print(f"  {slug:<24}{kick:<14}{lines:>6}{tags:>6}  {cta}")
    print()
    if problems:
        for p in problems:
            print(f"  ! {p}")
    else:
        print("  ✅ 字段齐全、标题行数与栏目名都收敛")
    out = args.out or os.path.join(args.dir, f"covers-{args.variant}.png")
    path = sheet(items, out, args.variant, args.cols)
    print(f"\n  拼图：{path or '（没有可用封面图）'}\n")


if __name__ == "__main__":
    main()
