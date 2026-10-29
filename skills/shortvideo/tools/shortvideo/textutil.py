"""文字度量工具：给"语速体检"和评分器提供统一的音节口径。

为什么要音节而不是字数：中文数字串读出来比看到的字少得多——
"8000" 写 4 个字符，读作"八千"只有 2 个音节；"33%" 写 3 个字符，读作"百分之三十三"6 个音节。
按字符算会把这类稿子误判成"语速过快"，按音节算才接近真实口播节奏。
"""
import re

_DIGITS = "零一二三四五六七八九"
_UNITS = ["", "十", "百", "千"]
_BIG = ["", "万", "亿", "万亿"]


def int_to_cn(n):
    """整数转中文读法（0 < n < 10^12），只要够算音节数即可。"""
    n = int(n)
    if n == 0:
        return "零"
    groups = []
    while n > 0:
        groups.append(n % 10000)
        n //= 10000
    parts = []
    for gi in range(len(groups) - 1, -1, -1):
        g = groups[gi]
        if g == 0:
            continue
        s, zero = "", False
        for pos in range(3, -1, -1):
            d = (g // (10 ** pos)) % 10
            if d == 0:
                zero = True
                continue
            if zero and s:
                s += "零"
            zero = False
            s += _DIGITS[d] + _UNITS[pos]
        parts.append(s + _BIG[gi])
    out = "".join(parts)
    # 十位开头读"十"而不是"一十"
    if out.startswith("一十"):
        out = out[1:]
    return out


def syllables(text):
    """估算一段中文的朗读音节数（数字/百分号/小数点按读法折算）。"""
    if not text:
        return 0
    t = str(text)
    n = 0
    # 先处理数字（含小数、百分号、千分位逗号）
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?%?", t):
        tok = m.group(0)
        pct = tok.endswith("%")
        core = tok.rstrip("%").replace(",", "")
        if "." in core:
            ip, fp = core.split(".", 1)
            n += len(int_to_cn(ip)) + 1 + len(int_to_cn(fp))   # 小数点读"点"
        else:
            n += len(int_to_cn(core))
        if pct:
            n += 3                                            # "百分之"
    # 去掉数字后，汉字/字母各算 1 个音节，标点不计
    rest = re.sub(r"\d[\d,]*(?:\.\d+)?%?", "", t)
    n += len(re.findall(r"[\u4e00-\u9fa5A-Za-z]", rest))
    return n


def speech_rate(text, seconds):
    """音节/秒。"""
    if not seconds or seconds <= 0:
        return 0.0
    return syllables(text) / seconds


_CN_DIGIT = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
             "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNIT = {"十": 10, "百": 100, "千": 1000}
_CN_BIG = {"万": 10 ** 4, "亿": 10 ** 8}


def cn_to_num(text):
    """把一段中文数字（如 四百五十 / 零点一 / 五十二）转成 float；失败返回 None。"""
    if not text:
        return None
    t = text.strip()
    if t in ("十",):
        return 10.0
    int_part, _, frac_part = t.partition("点")
    total, section, num = 0, 0, 0
    for ch in int_part:
        if ch in _CN_DIGIT:
            num = _CN_DIGIT[ch]
        elif ch in _CN_UNIT:
            section += (num or 1) * _CN_UNIT[ch]
            num = 0
        elif ch in _CN_BIG:
            section = (section + num) * _CN_BIG[ch]
            total += section
            section, num = 0, 0
        else:
            return None
    val = total + section + num
    if frac_part:
        digits = "".join(str(_CN_DIGIT[c]) for c in frac_part if c in _CN_DIGIT)
        if not digits:
            return None
        val += float("0." + digits)
    return float(val)


_NUM_TOKEN = re.compile(
    r"\d+(?:[.,]\d+)*"
    r"|[零〇一二两三四五六七八九十百千万亿]+点[零〇一二两三四五六七八九]+"
    r"|[零〇一二两三四五六七八九十百千万亿]+")


_CN_ALL_DIGITS = "零〇一二两三四五六七八九"


def _cn_value(tok, nxt=""):
    """单个中文数字 token 的数值。比 cn_to_num 多处理两种口语写法：

    1. 量词尾缀：「五十三千帕」= 53 kPa，这里的"千"是单位前缀不是乘法
       （中文里 53000 会念"五万三千"），旧实现给 3050。
    2. 逐位年份：「一九五四年」= 1954，旧实现给 4。
    3. 单独的"万/千"后面跟名词（「万米高空」）按 1000/10000 计。
    """
    if not tok:
        return None
    # 逐位读法：全部由数字字组成且长度 >1
    if len(tok) > 1 and all(ch in _CN_ALL_DIGITS for ch in tok):
        return float("".join(str(_CN_DIGIT[ch]) for ch in tok))
    # 量词尾缀：「五十三千帕」= 53 kPa，"千"是单位前缀。
    # 只有当被剥掉的头本身已经带单位（十/百/千/万）时才剥——
    # 否则「三百度」会被剥成 3（那是 300，百 是乘数不是单位）。
    if len(tok) > 1 and tok[-1] in "千百" and nxt and not nxt.isdigit():
        head_txt = tok[:-1]
        if len(head_txt) > 1 and any(u in head_txt for u in "十百千万亿"):
            head = cn_to_num(head_txt)
            if head is not None and head != 0:
                return head
    v = cn_to_num(tok)
    if v == 0 and tok in "千百十万亿":
        return float({"千": 1000, "百": 100, "十": 10, "万": 10000, "亿": 100000000}[tok])
    return v


def numbers_in(text):
    """抽出一段中英混排文字里的全部数值（中文数字也解析）。返回 set[float]。"""
    text = str(text or "")
    out = set()
    for m in _NUM_TOKEN.finditer(text):
        tok = m.group(0).replace(",", "")
        if re.fullmatch(r"\d+(\.\d+)?", tok):
            out.add(float(tok))
            continue
        nxt = text[m.end():m.end() + 1]
        v = _cn_value(tok, nxt)
        if v is not None and v != 0:      # 0 出现在口播里几乎都是解析噪声
            out.add(v)
    return out
