"""配音层（WSL/Linux 版）：edge-tts 神经音色 + mood 情绪预设 + 静音兜底。

相对 Windows 版的差异 / 增强：
  - 无 SAPI，断网直接降级静音占位（时长按字数估算，流水线不中断）
  - mp3 -> wav 用 imageio-ffmpeg 内置 ffmpeg
  - 企业 MITM 环境：把系统 CA（/etc/ssl/certs/ca-certificates.crt 等）补进
    edge_tts 的 WSS 证书链，并把 https_proxy 显式传给 aiohttp（两者缺失时静默跳过）
  - synth_all(): 多镜头并发合成（有界并发、顺序保持、单项失败降级静音）
  - concat()/ _trim(): array('h')/bytearray 流式写入（可选 numpy 加速），
    避免整条时间轴 struct.pack(*百万个参数)
  - 可选 wav 缓存：<wav 同目录>/tts_cache/<sha1>.wav，命中即跳过网络

公开接口保持向后兼容（只做加法）：
  MOODS, ffmpeg_exe(), EdgeVoice(name, rate, pitch, retries).synth(text, path, mood),
  SilentVoice, build_voice(cfg), concat(segments, total, out_path)
新增：
  VOICE_PRESETS / VOICE_MOODS（3 个人格音色 + 每音色 5 情绪韵律）
  build_moods(preset, tuning), resolve_preset(cfg), preset_name(key), list_presets(),
  normalize_prosody(field, value)
  响度对齐：speech_levels(), parse_normalize(), volume_relative_db()（cfg["normalize"]）
  词级时间戳：voice.words_for(wav) / voice.last_words / voice.words_by_id[shot_id],
  read_words(wav), shift_words(), words_duration(), boundary_supported()（cfg["words"]）
  synth_all(voice, items, jobs=None) -> [duration...]
  patch_edge_ssl(), resolve_proxy(value), synth_all
"""
import array
import asyncio
import hashlib
import inspect
import json
import math
import os
import random
import ssl
import subprocess
import sys
import threading
import wave

SAMPLE_RATE = 22050
CHANNELS = 1
SAMPWIDTH = 2

# 字数 -> 时长估算（中文神经音色约 6 字/秒）
CHARS_PER_SEC = 6.2
# 静音检测阈值 / 前后留白
TRIM_THRESH = 420
TRIM_PAD = 0.06
# 并发合成默认并发度（edge-tts 单个 WSS 连接较短，4 路足够且稳）
DEFAULT_JOBS = 3

# 响度对齐：同一音色对不同句子会给出 ±2~4dB 的天然差异（实测 yunxi 的 explain
# 比同音色其它句子轻 4.4dB），五段连播就会出现“忽大忽小”的电平跳变。
# 这里按“语音段 RMS”把每条对到统一目标，再把 mood 的 volume 相对增益加回去，
# 于是：跨镜头电平一致，情绪强弱仍保留。
NORMALIZE_TARGET_DBFS = -20.0   # 语音段 RMS 目标电平（门限 RMS，剔除停顿）
NORMALIZE_GAIN_LIMIT_DB = 12.0  # 单条最大增益修正（防止把底噪/静音段抬起来）
NORMALIZE_PEAK_CEILING_DBFS = -0.5  # 峰值保护，留 0.5dB 余量

# 五段式情绪（hook 钩子 / rule 规则 / explain 原理 / reveal 揭晓 / win 收尾）
MOOD_KEYS = ("hook", "rule", "explain", "reveal", "win")

# 通用默认韵律表：保持原样，供没有绑定 preset 的旧音色（只给 name 的配置）继续使用。
MOODS = {
    "hook":    dict(rate="+12%", pitch="+3Hz", volume="+0%"),
    "rule":    dict(rate="+2%",  pitch="+0Hz", volume="+0%"),
    "explain": dict(rate="-4%",  pitch="-1Hz", volume="+0%"),
    "reveal":  dict(rate="+6%",  pitch="+2Hz", volume="+6%"),
    "win":     dict(rate="+10%", pitch="+2Hz", volume="+8%"),
}

# ------------------------------------------------------------------ 音色注册表
#
# 只要 3 个音色，但每个音色的情绪人格必须不一样：
#   xiaoxiao 亮、快、笑点高 —— 科普讲解万能主力
#   yunxi    轻快带调侃，语速弹性最大 —— 反常识/冷知识
#   yunjian  低、慢、厚 —— 悬念与重磅结论（低音色不能配 +12% 语速，会飘）
#
# base 是这个音色的“人格基准韵律”（content.json 里显式写了 rate/pitch/volume 时以配置为准）。
# 选型依据见 tools/voice_lab.py 的实测指标（F0 中位数 / 字速 / 响度）。
VOICE_PRESETS = {
    "xiaoxiao": dict(
        name="zh-CN-XiaoxiaoNeural",
        label="活泼亲切女主",
        persona="明亮亲和、带笑意，句尾自然上扬，节奏轻快不聒噪",
        best_for=("科普讲解主推", "生活向·好奇心题材", "泛人群/女性受众"),
        base=dict(rate="+0%", pitch="+0Hz", volume="+0%"),
    ),
    "yunxi": dict(
        name="zh-CN-YunxiNeural",
        label="阳光邻家男声",
        persona="邻家感、带点俏皮调侃，语速弹性大，反问句最抓耳",
        best_for=("反常识题材", "轻科普/冷知识", "年轻受众"),
        base=dict(rate="+2%", pitch="+0Hz", volume="+0%"),
    ),
    "yunjian": dict(
        name="zh-CN-YunjianNeural",
        label="浑厚沉稳男声",
        persona="低沉厚重、语速克制，留白多，压得住悬念和重磅结论",
        best_for=("悬念/揭秘", "重磅结论", "硬核原理解析"),
        base=dict(rate="-3%", pitch="-2Hz", volume="+0%"),
    ),
}

DEFAULT_PRESET = "xiaoxiao"

# 每个音色的 5 种情绪韵律（叠加在 MOODS 之上的覆盖表）。
# 设计口径（实测依据见 tools/voice_lab.py 的“控制变量诊断”，同一句文本 × 5 情绪）：
#   - rate：hook 比 explain 快 10~20%（实测时长差 -11%~-16%，可测）
#   - pitch：按音色音区换算，explain 最低、hook/win 最高，跨度约 1~1.5 个半音
#     （265Hz 女声用 16Hz，165Hz 男声用 14Hz，130Hz 浑厚男声用 10Hz；
#      太小（±2Hz）在实测里会被句子语调盖掉，等于没做情绪）
#   - volume：±8% 幅度约 0.6dB —— 配合 loudness 对齐后仍保留这 0.6dB 的强弱意图
# 低音色不靠“加速”制造紧张：yunjian 的 reveal 是“更低 + 更响”，不是更快。
VOICE_MOODS = {
    "xiaoxiao": {
        "hook":    dict(rate="+14%", pitch="+5Hz",  volume="+2%"),
        "rule":    dict(rate="+4%",  pitch="+1Hz",  volume="+0%"),
        "explain": dict(rate="-3%",  pitch="-8Hz",  volume="+0%"),
        "reveal":  dict(rate="+3%",  pitch="+4Hz",  volume="+6%"),
        "win":     dict(rate="+12%", pitch="+8Hz",  volume="+8%"),
    },
    "yunxi": {
        "hook":    dict(rate="+16%", pitch="+4Hz",  volume="+3%"),
        "rule":    dict(rate="+7%",  pitch="+0Hz",  volume="+0%"),
        "explain": dict(rate="-1%",  pitch="-7Hz",  volume="+0%"),
        "reveal":  dict(rate="+5%",  pitch="+3Hz",  volume="+5%"),
        "win":     dict(rate="+13%", pitch="+7Hz",  volume="+7%"),
    },
    "yunjian": {
        "hook":    dict(rate="+4%",  pitch="-2Hz",  volume="+4%"),
        "rule":    dict(rate="-2%",  pitch="-3Hz",  volume="+0%"),
        "explain": dict(rate="-8%",  pitch="-6Hz",  volume="+0%"),
        "reveal":  dict(rate="-4%",  pitch="-9Hz",  volume="+8%"),
        "win":     dict(rate="+2%",  pitch="+1Hz",  volume="+6%"),
    },
}

# edge-tts / Azure 的合法区间：rate、volume 百分比，pitch 赫兹；超出会被服务端拒绝。
_PROSODY_LIMITS = {
    "rate": ("%", -50.0, 100.0),
    "pitch": ("Hz", -50.0, 50.0),
    "volume": ("%", -50.0, 50.0),
}


def normalize_prosody(field, value, fallback=None):
    """把 content.json 里的 rate/pitch/volume 归一化成 edge-tts 认的字符串。

    接受 "+12%"、"12"、12、"-3Hz"、"+2hz" 等写法；非法/超限时钳到区间内，
    实在解析不了就回退 fallback（默认 +0% / +0Hz），绝不抛异常。
    """
    unit, lo, hi = _PROSODY_LIMITS[field]
    default = fallback if fallback is not None else ("+0Hz" if unit == "Hz" else "+0%")
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        num = float(value)
    elif isinstance(value, str):
        s = value.strip().replace(" ", "")
        if not s:
            return default
        low = s.lower()
        for suffix in ("percent", "%"):
            if low.endswith(suffix):
                s = s[: -len(suffix)]
                break
        else:
            if low.endswith("hz"):
                s = s[:-2]
        try:
            num = float(s)
        except ValueError:
            _warn_once(f"prosody-{field}-{value}",
                       f"[voice] {field}={value!r} 无法解析，已用默认 {default}")
            return default
    else:
        return default
    if num != num or num in (float("inf"), float("-inf")):
        return default
    if not (lo <= num <= hi):
        _warn_once(f"prosody-clamp-{field}-{num}",
                   f"[voice] {field}={num:g} 超出 edge-tts 区间 [{lo:g}, {hi:g}]，已钳位")
        num = max(lo, min(hi, num))
    if unit == "Hz":
        return f"{int(round(num)):+d}Hz"
    return f"{round(num, 1):+g}%"


def build_moods(preset=None, tuning=None):
    """合成某个音色的完整 5 情绪韵律表：MOODS -> VOICE_MOODS[preset] -> mood_tuning。

    preset 为 None/未知时就是 MOODS 的副本（旧配置的行为完全不变）。
    """
    table = {k: dict(v) for k, v in MOODS.items()}
    over = VOICE_MOODS.get((preset or "").strip().lower())
    if over:
        for mood, pros in over.items():
            table.setdefault(mood, {}).update(dict(pros))
    for mood, pros in dict(tuning or {}).items():
        if not isinstance(pros, dict) or mood not in table:
            _warn_once(f"tuning-{mood}",
                       f"[voice] mood_tuning 里的 '{mood}' 不可用，已忽略"
                       f"（可选：{', '.join(MOOD_KEYS)}）")
            continue
        for field in ("rate", "pitch", "volume"):
            if field in pros:
                table[mood][field] = normalize_prosody(field, pros[field],
                                                       table[mood].get(field))
    for pros in table.values():
        for field in ("rate", "pitch", "volume"):
            pros[field] = normalize_prosody(field, pros.get(field))
    return table


def resolve_preset(cfg):
    """配置（或 preset 键 / 音色名）-> 注册表 key。

    优先级：preset（或 voice_preset）> name 同名映射 > DEFAULT_PRESET。
    传字符串且不是已知 preset/音色名时返回 None（便于探测）；传配置字典时总有兜底值。
    """
    if isinstance(cfg, str):
        key = cfg.strip().lower()
        if key in VOICE_PRESETS:
            return key
        for k, meta in VOICE_PRESETS.items():
            if meta["name"].lower() == key:
                return k
        return None
    cfg = dict(cfg or {})
    name = cfg.get("name")
    raw = cfg.get("preset", cfg.get("voice_preset"))
    if raw is not None and str(raw).strip():
        key = resolve_preset(str(raw))
        if key:
            return key
        _warn_once(f"preset-{raw}",
                   f"[voice] 未知 preset {raw!r}，已退回默认音色"
                   f"（可选：{', '.join(VOICE_PRESETS)}）")
        return resolve_preset(str(name)) if name else DEFAULT_PRESET
    if name:
        return resolve_preset(str(name))  # 只写 name 的老配置也能吃到对应情绪表
    return DEFAULT_PRESET


def preset_name(key):
    """preset key -> edge-tts 音色名（未知 key 返回 None）。"""
    meta = VOICE_PRESETS.get(str(key or "").strip().lower())
    return meta["name"] if meta else None


def list_presets():
    """[(key, label, name, best_for), ...]，供 CLI/文档/菜单使用。"""
    return [(k, m["label"], m["name"], m["best_for"]) for k, m in VOICE_PRESETS.items()]

# 系统 CA bundle 常见位置（Debian/Ubuntu、RHEL、Alpine、pip 自带）
_SYSTEM_CA_CANDIDATES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/cert.pem",
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/usr/local/share/ca-certificates/ca-certificates.crt",
)
_PROXY_ENV_KEYS = ("https_proxy", "HTTPS_PROXY", "http_proxy", "HTTP_PROXY")

_warn_lock = threading.Lock()
_warned = set()


def _warn_once(key, message):
    """同一类告警只打印一次，避免 5 个镜头刷 5 遍。"""
    with _warn_lock:
        if key in _warned:
            return
        _warned.add(key)
    print(message, flush=True)


# ------------------------------------------------------------------ 可选 numpy

_NUMPY = None
_NUMPY_TRIED = False


def _numpy():
    global _NUMPY, _NUMPY_TRIED
    if not _NUMPY_TRIED:
        _NUMPY_TRIED = True
        try:  # numpy 只是加速项，缺了也要能跑
            import numpy as _np
            _NUMPY = _np
        except Exception:  # noqa: BLE001
            _NUMPY = None
    return _NUMPY


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ------------------------------------------------------------------ TLS / 代理

_ssl_state = None
_ssl_lock = threading.Lock()


def _system_ca_files():
    return [p for p in _SYSTEM_CA_CANDIDATES if os.path.isfile(p)]


def patch_edge_ssl(verbose=False, rebuild=False):
    """让 edge-tts 的 WSS 握手信任本机系统 CA（企业 MITM 网关必需）。

    edge_tts.communicate 在模块级写死 `_SSL_CTX = create_default_context(cafile=certifi.where())`，
    只带 certifi 的公网根证书；本机 TLS 被企业 CA 重新签发，握手必然失败。
    这里在 certifi 基础上叠加系统 CA bundle。幂等、任何缺失都静默降级（返回 False）。
    """
    global _ssl_state
    with _ssl_lock:
        if _ssl_state is not None and not rebuild:
            return _ssl_state
        try:
            from edge_tts import communicate as _comm
        except Exception as e:  # noqa: BLE001  edge-tts 没装：交给上层降级静音
            if verbose:
                print(f"[voice] edge-tts 不可导入（{e}）")
            _ssl_state = False
            return False

        ctx = None
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:  # noqa: BLE001  certifi 缺失/证书文件损坏 -> 系统默认
            try:
                ctx = ssl.create_default_context()
            except Exception:  # noqa: BLE001
                ctx = None
        if ctx is not None:
            for ca in _system_ca_files():
                try:
                    ctx.load_verify_locations(ca)
                except Exception:  # noqa: BLE001
                    pass
            try:
                _comm._SSL_CTX = ctx
                _ssl_state = True
                return True
            except Exception:  # noqa: BLE001
                pass
        _ssl_state = False
        if verbose:
            print("[voice] 未能增强 edge-tts 的 SSL 上下文（缺 certifi / 系统 CA）")
        return False


def ssl_ready():
    """返回 SSL 上下文是否已增强（None=未探测）。"""
    return _ssl_state


def _env_proxy():
    for k in _PROXY_ENV_KEYS:
        v = os.environ.get(k)
        if v and v.strip():
            return v.strip()
    return None


def _host_bypassed(proxy, host):
    """no_proxy 命中时返回 True（让代理配置真正生效，而不是永远走代理）。"""
    no_proxy = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
    if not no_proxy or not host:
        return False
    host = host.lower()
    for item in no_proxy.split(","):
        item = item.strip().lower().lstrip("*").lstrip(".")
        if item and (host == item or host.endswith("." + item)):
            return True
    return False


def resolve_proxy(value="auto", host="speech.platform.bing.com"):
    """把 voice.proxy 配置解析成 aiohttp 可用的代理串。

    "auto"/None/缺失 -> 读 https_proxy/HTTPS_PROXY/http_proxy/HTTP_PROXY（并尊重 no_proxy）
    ""/"none"/"off"/"direct"/False -> 不用代理
    其他字符串 -> 直接当作代理 URL（例如 http://proxy.corp:8080）
    """
    if value is None:
        value = "auto"
    if isinstance(value, bool):
        proxy = _env_proxy() if value else None
    elif isinstance(value, str):
        v = value.strip()
        low = v.lower()
        if low in ("", "none", "off", "no", "false", "direct", "disabled"):
            proxy = None
        elif low in ("auto", "env", "default", "1", "true", "yes"):
            proxy = _env_proxy()
        else:
            proxy = v
    else:
        proxy = _env_proxy()
    if proxy and host and _host_bypassed(proxy, host):
        return None
    return proxy


# ------------------------------------------------------------------ wav 工具

def _ensure_dir(path):
    d = os.path.dirname(os.path.abspath(path))
    if d:
        os.makedirs(d, exist_ok=True)


def _le_bytes(arr):
    """array('h') -> WAV 要求的 little-endian 字节串（大端机自动翻转）。"""
    if sys.byteorder == "big" and len(arr):
        copy = array.array("h", arr.tobytes())
        copy.byteswap()
        return copy.tobytes()
    return arr.tobytes()


def _write_wav(path, arr):
    _ensure_dir(path)
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPWIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(_le_bytes(arr) if isinstance(arr, array.array) else bytes(arr))
    return path


def _read_wav(path):
    """读成 array('h')（22050Hz 单声道 s16）。参数不一致时做轻量归一化。"""
    with wave.open(path, "rb") as w:
        nch, sw, sr = w.getnchannels(), w.getsampwidth(), w.getframerate()
        raw = w.readframes(w.getnframes())
    if sw == 2:
        arr = array.array("h")
        arr.frombytes(raw)
    elif sw == 1:  # 8bit 无符号 -> s16
        arr = array.array("h", ((b - 128) << 8 for b in raw))
    elif sw == 4:  # 32bit -> 取高 16 位
        a32 = array.array("i")
        a32.frombytes(raw)
        arr = array.array("h", (max(-32768, min(32767, v >> 16)) for v in a32))
    else:
        raise ValueError(f"unsupported sample width {sw}: {path}")
    if sys.byteorder == "big" and len(arr):
        arr.byteswap()
    if nch == 2:  # 下混单声道
        arr = array.array("h", ((arr[i] + arr[i + 1]) // 2
                                for i in range(0, len(arr) - 1, 2)))
    elif nch > 2:
        arr = array.array("h", arr[::nch])
    if sr != SAMPLE_RATE and len(arr):
        arr = _resample(arr, sr, SAMPLE_RATE)
    return arr


def _resample(arr, sr_from, sr_to):
    n_out = int(len(arr) * sr_to / sr_from)
    if n_out <= 0:
        return array.array("h")
    step = sr_from / sr_to
    out = array.array("h", bytes(2 * n_out))
    for i in range(n_out):
        out[i] = arr[min(len(arr) - 1, int(i * step))]
    return out


def _first_above(arr, thresh):
    """返回首个 |sample| > thresh 的下标，没有则 -1。分块 max/min（C 层）快扫。"""
    np = _numpy()
    if np is not None:
        idx = np.flatnonzero(np.abs(np.frombuffer(arr.tobytes(), dtype="<i2")) > thresh)
        return int(idx[0]) if idx.size else -1
    chunk = 8192
    n = len(arr)
    for base in range(0, n, chunk):
        blk = arr[base:base + chunk]
        if max(blk) > thresh or min(blk) < -thresh:
            for i, v in enumerate(blk):
                if v > thresh or v < -thresh:
                    return base + i
    return -1


def _last_above(arr, thresh):
    """返回最后一个 |sample| > thresh 的下标，没有则 -1。"""
    np = _numpy()
    if np is not None:
        idx = np.flatnonzero(np.abs(np.frombuffer(arr.tobytes(), dtype="<i2")) > thresh)
        return int(idx[-1]) if idx.size else -1
    chunk = 8192
    n = len(arr)
    for end in range(n, 0, -chunk):
        base = max(0, end - chunk)
        blk = arr[base:end]
        if max(blk) > thresh or min(blk) < -thresh:
            for i in range(len(blk) - 1, -1, -1):
                v = blk[i]
                if v > thresh or v < -thresh:
                    return base + i
    return -1


def _trim_ex(path, thresh=TRIM_THRESH, pad=TRIM_PAD):
    """裁掉首尾静音，原地重写 wav。

    返回 (时长秒, 头部裁掉秒数, 尾部裁掉秒数)。头尾偏移是给“词级时间戳”用的：
    edge-tts 的 offset 以未裁剪音频的起点为准，裁剪后必须整体左移。
    """
    arr = _read_wav(path)
    n = len(arr)
    p = int(SAMPLE_RATE * pad)
    if n:
        head = _first_above(arr, thresh)
        last = _last_above(arr, thresh)
        if head < 0 or last < 0:  # 整段静音：保持原样（与旧实现一致）
            head, tail = 0, n
        else:
            head = max(0, head - p)
            tail = min(n, last + 1 + p)
    else:
        head = tail = 0
    kept = arr[head:tail]
    _write_wav(path, kept)
    return len(kept) / SAMPLE_RATE, head / SAMPLE_RATE, (n - tail) / SAMPLE_RATE


def _trim(path, thresh=TRIM_THRESH, pad=TRIM_PAD):
    """裁掉首尾静音，原地重写 wav，返回时长（秒）。"""
    return _trim_ex(path, thresh, pad)[0]


def _silence(path, seconds):
    _ensure_dir(path)
    nsamp = max(0, int(SAMPLE_RATE * seconds))
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPWIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"\x00\x00" * nsamp)
    return seconds


# ------------------------------------------------------------ 词级时间戳（卡拉OK 字幕）

WORDS_SIDECAR_SUFFIX = ".words.json"
_BOUNDARY_SUPPORTED = None


def boundary_supported():
    """当前 edge-tts 是否支持 Communicate(boundary="WordBoundary")。"""
    global _BOUNDARY_SUPPORTED
    if _BOUNDARY_SUPPORTED is None:
        try:
            import edge_tts
            _BOUNDARY_SUPPORTED = "boundary" in inspect.signature(
                edge_tts.Communicate.__init__).parameters
        except Exception:  # noqa: BLE001
            _BOUNDARY_SUPPORTED = False
    return bool(_BOUNDARY_SUPPORTED)


def chunk_word(chunk):
    """edge-tts 的 WordBoundary chunk -> {"text","start","end"}（秒，相对音频起点）。

    edge-tts 的 offset/duration 单位是 100ns（tick），除以 1e7 得秒。
    """
    offset = int(chunk.get("offset") or 0)
    dur = int(chunk.get("duration") or 0)
    text = str(chunk.get("text") or "").strip()
    if not text:
        return None
    return dict(text=text, start=round(offset / 1e7, 3), end=round((offset + dur) / 1e7, 3))


def shift_words(words, head, duration):
    """把“未裁剪音频”时间轴上的词，平移到裁剪后的 wav 时间轴并夹到 [0, duration]。

    返回 [{"text","start","end"}]，start/end 单位秒、相对该 wav 起点。
    """
    out = []
    for w in words or []:
        start = max(0.0, float(w.get("start") or 0.0) - head)
        end = min(float(duration), float(w.get("end") or 0.0) - head)
        text = str(w.get("text") or "").strip()
        if not text or end - start <= 0.001:
            continue
        out.append(dict(text=text, start=round(start, 3), end=round(end, 3)))
    return out


def words_duration(words):
    """词序列覆盖到的结束时刻（没有词返回 0）。"""
    return round(max((float(w.get("end") or 0.0) for w in words or []), default=0.0), 3)


def read_words(wav_path):
    """读 <wav>.words.json 旁车文件（跨进程可用：渲染阶段单独跑也能拿到）。

    文件缺失/损坏一律返回 []，绝不抛异常。
    """
    try:
        with open(str(wav_path) + WORDS_SIDECAR_SUFFIX, encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("words") if isinstance(data, dict) else data
        out = []
        for w in items or []:
            text = str(w.get("text") or "").strip()
            if text:
                out.append(dict(text=text, start=float(w["start"]), end=float(w["end"])))
        return out
    except Exception:  # noqa: BLE001
        return []


def _words_cache_path(wav_cache_path):
    base = wav_cache_path[:-4] if wav_cache_path.endswith(".wav") else wav_cache_path
    return base + WORDS_SIDECAR_SUFFIX


# ------------------------------------------------------------ 响度（dBFS + 对齐）

def speech_levels(arr, frame_s=0.02, gate_db=-20.0):
    """返回 (语音段 RMS dBFS, 峰值 dBFS)。

    “语音段 RMS”只看响过最高帧 gate_db 的 20ms 帧——整体 RMS 会被句子里的停顿
    拖低，用它对齐会把“停顿多”误判成“轻声”。
    """
    n = len(arr)
    if not n:
        return float("-inf"), float("-inf")
    peak = max(max(arr), -min(arr)) / 32768.0
    peak_db = 20.0 * math.log10(peak) if peak > 0 else float("-inf")
    np = _numpy()
    if np is not None:
        a = np.frombuffer(array.array("h", arr).tobytes(), dtype="<i2").astype("float64")
        frame = max(1, int(SAMPLE_RATE * frame_s))
        m = len(a) // frame
        if m <= 0:
            return float("-inf"), peak_db
        energy = (a[:m * frame].reshape(m, frame) ** 2).mean(axis=1)
        keep = energy[energy >= energy.max() * (10.0 ** (gate_db / 10.0))]
        if keep.size == 0 or keep.mean() <= 0:
            return float("-inf"), peak_db
        return 10.0 * math.log10(keep.mean() / (32768.0 ** 2)), peak_db
    frame = max(1, int(SAMPLE_RATE * frame_s))
    energies = []
    for st in range(0, n - frame + 1, frame):
        s = 0
        for v in arr[st:st + frame]:
            s += v * v
        energies.append(s / float(frame))
    if not energies:
        return float("-inf"), peak_db
    top = max(energies)
    if top <= 0:
        return float("-inf"), peak_db
    keep = [e for e in energies if e >= top * (10.0 ** (gate_db / 10.0))]
    gated = sum(keep) / len(keep) / (32768.0 ** 2)
    if gated <= 0:
        return float("-inf"), peak_db
    return 10.0 * math.log10(gated), peak_db


def _scale_wav(path, arr, factor):
    """原地整体乘以增益系数（int16 截断保护后写回）。"""
    _write_wav(path, _scale_samples(arr, factor))
    return path


def _scale_samples(arr, factor):
    np = _numpy()
    if np is not None:
        a = np.frombuffer(array.array("h", arr).tobytes(), dtype="<i2").astype("float64")
        out = np.clip(a * factor, -32768, 32767).astype("<i2")
        return array.array("h", out.tobytes())
    return array.array("h", (max(-32768, min(32767, int(round(v * factor)))) for v in arr))


def volume_relative_db(volume):
    """"+8%" -> 0.67（dB）：mood 的音量参数换算成相对增益。"""
    try:
        pct = float(str(volume or "+0%").replace("%", "").replace("+", ""))
    except (TypeError, ValueError):
        return 0.0
    factor = 1.0 + pct / 100.0
    if factor <= 0:
        return 0.0
    return 20.0 * math.log10(factor)


def parse_normalize(value):
    """content.json 的 normalize 键 -> 目标 dBFS 或 None（关闭）。

    true/auto/"on" -> 默认目标；数字或 "-18dB" -> 指定目标；false/off/"none" -> 关闭。
    """
    if value is None:
        return NORMALIZE_TARGET_DBFS
    if isinstance(value, bool):
        return NORMALIZE_TARGET_DBFS if value else None
    if isinstance(value, (int, float)):
        target = float(value)
    else:
        s = str(value).strip().lower()
        if s in ("", "0", "false", "off", "no", "none", "disabled"):
            return None
        if s in ("1", "true", "on", "yes", "auto", "db", "dbfs"):
            return NORMALIZE_TARGET_DBFS
        s = s.replace("dbfs", "").replace("db", "").replace("+", "")
        try:
            target = float(s)
        except ValueError:
            return NORMALIZE_TARGET_DBFS
    return max(-30.0, min(-10.0, target))


def estimate_duration(text):
    """无音频时的时长估算（沿用 6.2 字/秒，下限 2s）。"""
    return max(2.0, len(text or "") / CHARS_PER_SEC)


# 这些错误说明“网络/代理确定不可达”，重试无意义（区别于代理偶发 reset）
_FATAL_NET_HINTS = (
    "connection refused",
    "connect call failed",
    "no route to host",
    "name or service not known",
    "temporary failure in name resolution",
    "nodename nor servname provided",
    "errno 111",
    "errno -2",
    "errno -3",
    "getaddrinfo failed",
)


def _is_fatal_net_error(err):
    """TRUE = 网络/代理确定不可达（立刻熔断，别浪费重试）。"""
    try:
        txt = f"{type(err).__name__}: {err}".lower()
    except Exception:  # noqa: BLE001
        return False
    return any(h in txt for h in _FATAL_NET_HINTS)


def _fallback_silence(path, text, retries=0, err=None, verbose=True):
    """TTS 彻底失败时写等长静音，返回估算时长——永不抛异常。"""
    dur = estimate_duration(text)
    try:
        _silence(path, dur)
    except Exception:  # noqa: BLE001  连写盘都失败才最后放弃
        with wave.open(path, "wb") as w:
            w.setnchannels(CHANNELS)
            w.setsampwidth(SAMPWIDTH)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(b"\x00\x00" * int(SAMPLE_RATE * dur))
    if verbose:
        detail = f"{type(err).__name__}: {err}" if err is not None else "未知原因"
        if retries:
            detail = f"{retries} 次尝试均失败，最后一次 {detail}"
        _warn_once(
            "tts-fallback",
            f"[voice] edge-tts 不可用（{detail}），已降级静音占位 {dur:.2f}s"
            f"（字幕仍正常，流水线继续；如需真人配音请检查网络/代理/CA）",
        )
    return dur


# ------------------------------------------------------------------ 单线程内跑协程

def _run_coro(coro):
    """在当前线程无事件循环时直接 asyncio.run；意外嵌套时另起线程。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    box = {}

    def _worker():
        try:
            box["value"] = asyncio.run(coro)
        except BaseException as e:  # noqa: BLE001
            box["error"] = e

    t = threading.Thread(target=_worker, name="tts-asyncio")
    t.start()
    t.join()
    if "error" in box:
        raise box["error"]
    return box["value"]


# ------------------------------------------------------------------ 音色

class SilentVoice:
    """纯静音音色：没有网络也能出片（字幕正常）。"""

    label = "静音占位"
    persona = "无配音（字幕正常）"
    preset = None
    best_for = ()
    words_enabled = False

    def __init__(self):
        self.name = "silent"
        self.last_words = []
        self.words_by_path = {}
        self.words_by_id = {}
        self.cache_hits = 0

    def mood_prosody(self, mood=None):
        return dict(rate="+0%", pitch="+0Hz", volume="+0%")

    def words_for(self, path):
        """静音占位没有词时间戳（字幕回退到镜头级）。"""
        if not self.words_enabled:
            return []
        return read_words(path)

    def synth(self, text, path, mood=None):
        _ensure_dir(path)
        return _silence(path, estimate_duration(text))

    def synth_all(self, items, jobs=None):
        return [self.synth(it.get("text", ""), it["path"], it.get("mood"))
                for it in (items or [])]


class EdgeVoice:
    """edge-tts 神经音色：mood 情绪预设 + 重试 + 静音兜底 + 可选 wav 缓存。

    preset 绑定音色人格（见 VOICE_PRESETS）；每个 preset 有自己的 5 情绪韵律表
    （VOICE_MOODS），再被 cfg["mood_tuning"] 覆盖。self.moods 就是这张“棋盘”，
    synth() 时按 mood 查表。
    """

    def __init__(self, voice="zh-CN-XiaoxiaoNeural", rate="+0%", pitch="+0Hz", retries=4,
                 volume="+0%", proxy="auto", cache=True, cache_dir=None, jobs=None,
                 retry_delay=0.8, retry_passes=2, verbose=True, preset=None,
                 moods=None, mood_tuning=None, normalize=None, words=True):
        import edge_tts  # 没装则抛 ImportError，由 build_voice 降级
        self._edge = edge_tts
        self.voice, self.rate, self.pitch = voice, rate, pitch
        self.volume = volume
        self.preset = (preset or "").strip().lower() or None
        self.mood_tuning = dict(mood_tuning or {})
        self.moods = moods if moods is not None else build_moods(self.preset, self.mood_tuning)
        self.normalize_target = parse_normalize(normalize)
        self.words_enabled = bool(words)
        self.last_words = []        # 最近一条合成出来的词时间戳
        self.words_by_path = {}     # abspath(wav) -> [{"text","start","end"}]
        self.words_by_id = {}       # 镜头 id -> 同一份词时间戳（synth_all 时填）
        self.cache_hits = 0
        self.retries = max(1, int(retries or 1))
        self.retry_delay = max(0.0, float(retry_delay))
        self.retry_passes = max(1, int(retry_passes or 1))
        self.proxy = resolve_proxy(proxy)
        self.cache = bool(cache)
        self.cache_dir = cache_dir or None
        self.jobs = max(1, int(jobs)) if jobs else DEFAULT_JOBS
        self.verbose = bool(verbose)
        self.failures = 0
        self.offline = False  # 熔断：代理/网络确定不可达后不再浪费重试
        self.name = f"edge:{voice}"
        patch_edge_ssl(verbose=self.verbose)

    # ---------- 人格 / 韵律 ----------
    @property
    def meta(self):
        return VOICE_PRESETS.get(self.preset or "", {}) or {}

    @property
    def label(self):
        """中文人格名（静音兜底时是“静音占位”）；文档/日志用。"""
        return self.meta.get("label") or self.voice

    @property
    def persona(self):
        return self.meta.get("persona", "")

    @property
    def best_for(self):
        return tuple(self.meta.get("best_for") or ())

    def mood_prosody(self, mood=None):
        """mood -> 实际发给 edge-tts 的 rate/pitch/volume。

        未登记/未指定的 mood 回退到音色基础韵律（与旧行为一致）。
        """
        pros = self.moods.get(mood or "") if mood else None
        if not pros:
            return dict(rate=self.rate, pitch=self.pitch, volume=self.volume)
        return dict(rate=pros.get("rate", self.rate),
                    pitch=pros.get("pitch", self.pitch),
                    volume=pros.get("volume", self.volume))

    # ---------- 缓存 ----------
    def _cache_paths(self, path, key):
        d = self.cache_dir or os.path.join(os.path.dirname(os.path.abspath(path)), "tts_cache")
        return os.path.join(d, key + ".wav"), os.path.join(d, key + ".json")

    def _cache_key(self, text, mood):
        pros = self.mood_prosody(mood)
        sig = "|".join((
            text or "",
            self.voice,
            str(pros.get("rate", self.rate)),
            str(pros.get("pitch", self.pitch)),
            str(pros.get("volume", self.volume)),
            str(mood or ""),
            str(self.preset or ""),
            "" if self.normalize_target is None else f"norm{self.normalize_target:g}",
        ))
        return hashlib.sha1(sig.encode("utf-8")).hexdigest()

    def _cache_load(self, wav_path, target):
        """命中缓存时返回 (时长, 词时间戳|None)；未命中返回 None。"""
        if not self.cache:
            return None
        try:
            if not os.path.isfile(wav_path):
                return None
            with wave.open(wav_path, "rb") as w:
                dur = w.getnframes() / float(w.getframerate() or SAMPLE_RATE)
            _ensure_dir(target)
            with open(wav_path, "rb") as src, open(target, "wb") as dst:
                dst.write(src.read())
            words = None
            wpath = _words_cache_path(wav_path)
            if os.path.isfile(wpath):
                try:
                    with open(wpath, encoding="utf-8") as f:
                        words = json.load(f).get("words") or []
                except Exception:  # noqa: BLE001  坏文件 -> 当作没词
                    words = None
            return dur, words
        except Exception:  # noqa: BLE001  缓存坏了就当未命中
            return None

    def _cache_store(self, src, dur, wav_path, meta_path, words=None):
        if not self.cache:
            return
        try:
            os.makedirs(os.path.dirname(wav_path), exist_ok=True)
            with open(src, "rb") as f, open(wav_path, "wb") as g:
                g.write(f.read())
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"wav": os.path.basename(wav_path), "duration": dur,
                           "voice": self.voice, "size": os.path.getsize(wav_path),
                           "words": len(words or [])},
                          f, ensure_ascii=False)
            if words is not None:
                with open(_words_cache_path(wav_path), "w", encoding="utf-8") as f:
                    json.dump({"wav": os.path.basename(wav_path), "duration": dur,
                               "voice": self.voice, "words": words},
                              f, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 词时间戳 ----------
    def _remember_words(self, path, words, dur):
        words = [dict(text=w["text"], start=round(float(w["start"]), 3),
                      end=round(float(w["end"]), 3)) for w in (words or [])]
        self.last_words = words
        try:
            self.words_by_path[os.path.abspath(path)] = words
        except (TypeError, ValueError):
            pass
        if not self.words_enabled or not words:
            return   # 关了词时间戳，或这条本来就没拿到词 -> 不写旁车
        try:
            with open(str(path) + WORDS_SIDECAR_SUFFIX, "w", encoding="utf-8") as f:
                json.dump({"wav": os.path.basename(path), "duration": round(dur, 3),
                           "voice": self.voice, "words": words}, f, ensure_ascii=False)
        except OSError:
            pass

    def _forget_words(self, path):
        self.last_words = []
        try:
            self.words_by_path[os.path.abspath(path)] = []
            side = str(path) + WORDS_SIDECAR_SUFFIX
            if os.path.exists(side):
                os.remove(side)   # 这条降级成静音了，别留旧词时间戳误导字幕
        except OSError:
            pass

    def words_for(self, path):
        """取某条 wav 的词时间戳（秒，相对该 wav 起点）：内存 -> 旁车文件 -> []。

        cfg["words"]=False 时恒返回 []（要读历史旁车请直接用 tts.read_words(path)）。
        """
        if not self.words_enabled:
            return []
        key = os.path.abspath(path)
        if key in self.words_by_path:
            return list(self.words_by_path[key])
        return read_words(path)

    # ---------- 网络 ----------
    async def _fetch(self, text, path, mood):
        """拉一条音频。返回 (字节数, 词级时间戳原始列表)。"""
        pros = self.mood_prosody(mood)
        mp3 = path + ".mp3"
        kwargs = dict(
            rate=pros.get("rate", self.rate),
            pitch=pros.get("pitch", self.pitch),
            volume=pros.get("volume", self.volume),
        )
        if self.proxy:
            kwargs["proxy"] = self.proxy
        # edge-tts 7.x 默认只推 SentenceBoundary；要逐词高亮必须显式要 WordBoundary
        if self.words_enabled and boundary_supported():
            kwargs["boundary"] = "WordBoundary"
        comm = self._edge.Communicate(text, self.voice, **kwargs)
        size = 0
        words = []
        try:
            with open(mp3, "wb") as f:
                async for chunk in comm.stream():
                    ctype = chunk.get("type")
                    if ctype == "audio":
                        f.write(chunk["data"])
                        size += len(chunk["data"])
                    elif self.words_enabled and ctype in ("WordBoundary", "SentenceBoundary"):
                        word = chunk_word(chunk)
                        if word:
                            words.append(word)
            if size < 2000:
                raise RuntimeError(f"audio too small ({size} bytes)")
            await asyncio.to_thread(self._mp3_to_wav, mp3, path)
        finally:
            try:
                if os.path.exists(mp3):
                    os.remove(mp3)
            except OSError:
                pass
        return size, words

    @staticmethod
    def _mp3_to_wav(mp3, wav):
        subprocess.run(
            [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", mp3,
             "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
             "-sample_fmt", "s16", wav],
            check=True,
        )

    def _normalize_loudness(self, path, mood):
        """把这一条对到统一语音电平，再把 mood 的 volume 相对增益加回去。

        edge-tts 对不同句子给的电平天生差 2~4dB（实测），五段连播会听到电平跳变；
        对齐后跨镜头一致，而 reveal/win 的“更响一点”仍然保留。
        """
        if self.normalize_target is None:
            return None
        try:
            arr = _read_wav(path)
            speech_db, peak_db = speech_levels(arr)
            if speech_db == float("-inf"):
                return None
            rel_db = volume_relative_db(self.mood_prosody(mood).get("volume"))
            gain = self.normalize_target - speech_db + rel_db
            gain = max(-NORMALIZE_GAIN_LIMIT_DB, min(NORMALIZE_GAIN_LIMIT_DB, gain))
            if peak_db != float("-inf"):
                gain = min(gain, NORMALIZE_PEAK_CEILING_DBFS - peak_db)
            if abs(gain) < 0.05:
                return 0.0
            _scale_wav(path, arr, 10.0 ** (gain / 20.0))
            return round(gain, 2)
        except Exception as e:  # noqa: BLE001  对齐失败不影响成片
            _warn_once("norm-fail", f"[voice] 响度对齐失败（{type(e).__name__}: {e}），保留原始电平")
            return None

    async def _attempt(self, text, path, mood):
        """尝试合成一条（内部重试 + 退避），失败时抛异常，不做静音兜底。"""
        key = self._cache_key(text, mood)
        wav_c, meta_c = self._cache_paths(path, key)
        cached = self._cache_load(wav_c, path)
        if cached is not None:
            dur, words = cached
            self.cache_hits += 1
            await asyncio.to_thread(self._remember_words, path, words, dur)
            return dur
        if self.offline:
            raise RuntimeError("网络已被标记不可达（熔断），跳过网络合成")
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                _size, raw_words = await self._fetch(text, path, mood)
                dur, head, _tail = await asyncio.to_thread(_trim_ex, path)
                words = shift_words(raw_words, head, dur) if self.words_enabled else []
                await asyncio.to_thread(self._normalize_loudness, path, mood)
                self._cache_store(path, dur, wav_c, meta_c, words if self.words_enabled else None)
                await asyncio.to_thread(self._remember_words, path, words, dur)
                return dur
            except Exception as e:  # noqa: BLE001
                last = e
                if _is_fatal_net_error(e):
                    self.offline = True
                    _warn_once("tts-offline",
                               f"[voice] edge-tts 网络不可达（{type(e).__name__}: {e}），"
                               f"本次渲染其余镜头直接静音占位")
                    raise
                if attempt < self.retries:
                    patch_edge_ssl(rebuild=True, verbose=False)  # 每轮换新 SSL 上下文再试
                    delay = min(self.retry_delay * (2 ** (attempt - 1)), 6.0)
                    await asyncio.sleep(delay * (0.8 + 0.4 * random.random()))
        raise last if last is not None else RuntimeError("unknown edge-tts failure")

    async def _synth_async(self, text, path, mood=None, allow_fallback=True):
        """单条：缓存 -> 重试 -> 失败降级静音。默认永不抛异常。"""
        _ensure_dir(path)
        try:
            return await self._attempt(text, path, mood)
        except Exception as e:  # noqa: BLE001
            if not allow_fallback:
                raise
            self.failures += 1
            self._forget_words(path)   # 静音占位没有词时间戳，别让旧数据残留
            return _fallback_silence(path, text, self.retries, e, self.verbose)

    # ---------- 对外 ----------
    def synth(self, text, path, mood=None):
        return _run_coro(self._synth_async(text, path, mood))

    async def _synth_all_async(self, items, jobs):
        """有界并发 + 失败项整批补一轮 + 最终静音兜底；顺序与 items 一致。"""
        jobs = max(1, int(jobs))
        n = len(items)
        durs = [None] * n
        errors = {}
        for p in range(self.retry_passes):
            pending = [i for i in range(n) if durs[i] is None]
            if not pending:
                break
            if p and self.offline:  # 已确定断网，不必再补
                break
            sem = asyncio.Semaphore(jobs)

            async def one(i):
                item = items[i]
                async with sem:
                    try:
                        durs[i] = await self._synth_async(
                            item.get("text", ""), item["path"], item.get("mood"),
                            allow_fallback=False)
                        wid = item.get("id") or item.get("shot_id")
                        if wid and self.words_enabled:
                            words = self.words_by_path.get(os.path.abspath(item["path"]))
                            if words is not None:
                                self.words_by_id[str(wid)] = list(words)
                    except Exception as e:  # noqa: BLE001  单项失败不拖累其余
                        errors[i] = e

            await asyncio.gather(*(one(i) for i in pending))
            if p + 1 < self.retry_passes and any(durs[i] is None for i in range(n)):
                await asyncio.sleep(0.5)
        for i in range(n):
            if durs[i] is None:
                self.failures += 1
                self._forget_words(items[i]["path"])
                durs[i] = _fallback_silence(items[i]["path"], items[i].get("text", ""),
                                            self.retries, errors.get(i), self.verbose)
        return durs

    def synth_all(self, items, jobs=None):
        items = list(items or [])
        if not items:
            return []
        return _run_coro(self._synth_all_async(items, jobs or self.jobs))


def build_voice(cfg):
    """content.json 的 voice 字典 -> 音色对象。edge 不可用时静默降级 SilentVoice。

    可配置键（全部可选）：
      engine: "edge" | "silent"/"none"
      preset: 音色人格 key（xiaoxiao / yunxi / yunjian），见 VOICE_PRESETS。
              content.json 写 {"preset": "yunxi"} 即可，name 会自动补上；
              只写 name 的老配置依旧可用（同名音色会自动套用它的情绪表）。
      name / rate / pitch / volume: 音色与基础韵律（rate 等显式值覆盖 preset 的 base）
      mood_tuning: {mood: {rate/pitch/volume}}，按镜头微调单个情绪，例如
              {"preset": "yunjian", "mood_tuning": {"reveal": {"volume": "+10%"}}}
      normalize: 响度对齐（默认 true）。true = 对齐到 -20dBFS 语音电平；
              数字或 "-18dB" = 自定义目标；false/off = 关闭（保留 edge-tts 原始电平）。
              对齐后仍保留每个 mood 的 volume 强弱，跨镜头电平不再跳变。
      words: 词级时间戳（默认 true）。true = 向 edge-tts 要 WordBoundary，
              每个镜头产出 [{"text","start","end"}]（秒，相对该 wav），
              同时写 <wav>.words.json 旁车与 tts_cache/<sha1>.words.json；
              false = 不收集（省一点内存/IO，卡拉OK 字幕就没词可高亮）。
      retries: 每条重试次数（默认 4）      retry_delay: 退避基数秒（默认 0.8，指数退避+抖动）
      retry_passes: synth_all 整批补轮次数（默认 2）
      proxy: "auto"(默认, 读 https_proxy) | "" | "http://host:port"
      cache: true/false 或缓存目录路径（默认 true，落在 <wav目录>/tts_cache/）
      cache_dir: 缓存目录（覆盖默认）
      jobs: synth_all 默认并发度（默认 3）
    """
    cfg = dict(cfg or {})
    engine = str(cfg.get("engine") or "edge").strip().lower()
    if engine in ("silent", "none", "off", "mute", "empty"):
        return SilentVoice()
    cache = cfg.get("cache", True)
    cache_dir = cfg.get("cache_dir") or None
    if isinstance(cache, str):
        low = cache.strip().lower()
        if low in ("0", "false", "off", "no", "none", "disabled"):
            cache = False
        elif low in ("1", "true", "on", "yes", "auto", ""):
            cache = True
        else:  # 当作目录
            cache, cache_dir = True, cache_dir or cache
    preset = resolve_preset(cfg)
    base = (VOICE_PRESETS.get(preset or "", {}) or {}).get("base") or {}
    mood_tuning = cfg.get("mood_tuning") or cfg.get("moods") or {}
    if not isinstance(mood_tuning, dict):
        _warn_once("tuning-type", "[voice] mood_tuning 必须是 {mood: {...}} 字典，已忽略")
        mood_tuning = {}
    try:
        return EdgeVoice(
            cfg.get("name") or preset_name(preset) or "zh-CN-XiaoxiaoNeural",
            rate=normalize_prosody("rate", cfg.get("rate"), base.get("rate", "+0%")),
            pitch=normalize_prosody("pitch", cfg.get("pitch"), base.get("pitch", "+0Hz")),
            volume=normalize_prosody("volume", cfg.get("volume"), base.get("volume", "+0%")),
            retries=cfg.get("retries", 4),
            retry_delay=cfg.get("retry_delay", 0.8),
            retry_passes=cfg.get("retry_passes", 2),
            proxy=cfg.get("proxy", "auto"),
            cache=cache,
            cache_dir=cache_dir,
            jobs=cfg.get("jobs") or cfg.get("parallel"),
            verbose=cfg.get("verbose", True),
            preset=preset,
            moods=build_moods(preset, mood_tuning),
            mood_tuning=mood_tuning,
            normalize=cfg.get("normalize", cfg.get("loudness")),
            words=cfg.get("words", True),
        )
    except Exception as e:  # noqa: BLE001  edge-tts 没装 / 参数不合法
        print(f"[voice] edge 不可用（{e}），降级静音（字幕仍正常）", flush=True)
        return SilentVoice()


# ------------------------------------------------------------------ 多镜头并发

def synth_all(voice, items, jobs=None):
    """并发合成多个镜头。

    items = [{"text": ..., "path": ..., "mood": ...}, ...]
    返回 [duration, ...]，顺序与 items 严格一致；单项失败自动降级静音，绝不抛异常。
    """
    items = list(items or [])
    if not items:
        return []
    fn = getattr(voice, "synth_all", None)
    if callable(fn):
        try:
            accepts_jobs = "jobs" in inspect.signature(fn).parameters
        except (TypeError, ValueError):
            accepts_jobs = False
        return list(fn(items, jobs=jobs) if accepts_jobs else fn(items))
    # 外部自定义音色只有单条 synth()：用线程池兜底
    from concurrent.futures import ThreadPoolExecutor
    jobs = max(1, int(jobs or DEFAULT_JOBS))
    out = [None] * len(items)

    def one(i, it):
        try:
            out[i] = voice.synth(it.get("text", ""), it["path"], it.get("mood"))
        except Exception as e:  # noqa: BLE001
            out[i] = _fallback_silence(it["path"], it.get("text", ""), 0, e)

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        list(ex.map(lambda p: one(*p), enumerate(items)))
    return out


# ------------------------------------------------------------------ 时间轴拼接

_ZERO_BYTES = b"\x00\x00" * 65536  # 反复复用的静音块（128k 采样）


def _write_zeros(w, nsamp):
    while nsamp > 0:
        n = min(nsamp, 65536)
        w.writeframes(_ZERO_BYTES[: 2 * n])
        nsamp -= n


def _overlaps(segs):
    prev_end = -1
    for off, arr in segs:
        if off < prev_end:
            return True
        prev_end = off + len(arr)
    return False


def concat(segments, total, out_path):
    """把 (start_seconds, wav_path) 按时间轴拼成一条 22050Hz 单声道 wav。

    非重叠（正常流水线）走流式写入：只读一遍、不建整条时间轴数组，60s 约几毫秒；
    万一有重叠，退回整条缓冲区覆盖写（与旧实现语义一致：后者覆盖前者）。
    """
    _ensure_dir(out_path)
    nsamp = max(0, int(SAMPLE_RATE * total))
    segs = sorted(((int(start * SAMPLE_RATE), _read_wav(path)) for start, path in segments),
                  key=lambda t: t[0])
    np = _numpy()

    with wave.open(out_path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPWIDTH)
        w.setframerate(SAMPLE_RATE)
        if not _overlaps(segs):
            pos = 0
            for off, arr in segs:
                off = min(max(0, off), nsamp)
                if off > pos:
                    _write_zeros(w, off - pos)
                    pos = off
                room = nsamp - pos
                if room <= 0:
                    break
                piece = arr if len(arr) <= room else arr[:room]
                w.writeframes(_le_bytes(piece))
                pos += len(piece)
            if pos < nsamp:
                _write_zeros(w, nsamp - pos)
        elif np is not None:
            buf = np.zeros(nsamp, dtype="<i2")
            for off, arr in segs:
                a = np.frombuffer(arr.tobytes(), dtype="<i2")
                off = min(max(0, off), nsamp)
                n = min(len(a), nsamp - off)
                if n > 0:
                    buf[off:off + n] = a[:n]
            w.writeframes(buf.tobytes())
        else:
            track = array.array("h", bytes(2 * nsamp))
            for off, arr in segs:
                off = min(max(0, off), nsamp)
                n = min(len(arr), nsamp - off)
                if n > 0:
                    track[off:off + n] = arr[:n]
            if sys.byteorder == "big" and len(track):
                track.byteswap()
            w.writeframes(track.tobytes())
    return out_path
