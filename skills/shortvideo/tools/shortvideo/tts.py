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
  synth_all(voice, items, jobs=None) -> [duration...]
  patch_edge_ssl(), resolve_proxy(value), synth_all
"""
import array
import asyncio
import hashlib
import inspect
import json
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

MOODS = {
    "hook":    dict(rate="+12%", pitch="+3Hz", volume="+0%"),
    "rule":    dict(rate="+2%",  pitch="+0Hz", volume="+0%"),
    "explain": dict(rate="-4%",  pitch="-1Hz", volume="+0%"),
    "reveal":  dict(rate="+6%",  pitch="+2Hz", volume="+6%"),
    "win":     dict(rate="+10%", pitch="+2Hz", volume="+8%"),
}

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


def _trim(path, thresh=TRIM_THRESH, pad=TRIM_PAD):
    """裁掉首尾静音，原地重写 wav，返回时长（秒）。"""
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
    return len(kept) / SAMPLE_RATE


def _silence(path, seconds):
    _ensure_dir(path)
    nsamp = max(0, int(SAMPLE_RATE * seconds))
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPWIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"\x00\x00" * nsamp)
    return seconds


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

    def __init__(self):
        self.name = "silent"

    def synth(self, text, path, mood=None):
        _ensure_dir(path)
        return _silence(path, estimate_duration(text))

    def synth_all(self, items, jobs=None):
        return [self.synth(it.get("text", ""), it["path"], it.get("mood"))
                for it in (items or [])]


class EdgeVoice:
    """edge-tts 神经音色：mood 情绪预设 + 重试 + 静音兜底 + 可选 wav 缓存。"""

    def __init__(self, voice="zh-CN-XiaoxiaoNeural", rate="+0%", pitch="+0Hz", retries=4,
                 volume="+0%", proxy="auto", cache=True, cache_dir=None, jobs=None,
                 retry_delay=0.8, retry_passes=2, verbose=True):
        import edge_tts  # 没装则抛 ImportError，由 build_voice 降级
        self._edge = edge_tts
        self.voice, self.rate, self.pitch = voice, rate, pitch
        self.volume = volume
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

    # ---------- 缓存 ----------
    def _cache_paths(self, path, key):
        d = self.cache_dir or os.path.join(os.path.dirname(os.path.abspath(path)), "tts_cache")
        return os.path.join(d, key + ".wav"), os.path.join(d, key + ".json")

    def _cache_key(self, text, mood):
        pros = MOODS.get(mood, {})
        sig = "|".join((
            text or "",
            self.voice,
            str(pros.get("rate", self.rate)),
            str(pros.get("pitch", self.pitch)),
            str(pros.get("volume", self.volume)),
            str(mood or ""),
        ))
        return hashlib.sha1(sig.encode("utf-8")).hexdigest()

    def _cache_load(self, wav_path, target):
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
            return dur
        except Exception:  # noqa: BLE001  缓存坏了就当未命中
            return None

    def _cache_store(self, src, dur, wav_path, meta_path):
        if not self.cache:
            return
        try:
            os.makedirs(os.path.dirname(wav_path), exist_ok=True)
            with open(src, "rb") as f, open(wav_path, "wb") as g:
                g.write(f.read())
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump({"wav": os.path.basename(wav_path), "duration": dur,
                           "voice": self.voice, "size": os.path.getsize(wav_path)},
                          f, ensure_ascii=False)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 网络 ----------
    async def _fetch(self, text, path, mood):
        pros = MOODS.get(mood, {})
        mp3 = path + ".mp3"
        kwargs = dict(
            rate=pros.get("rate", self.rate),
            pitch=pros.get("pitch", self.pitch),
            volume=pros.get("volume", self.volume),
        )
        if self.proxy:
            kwargs["proxy"] = self.proxy
        comm = self._edge.Communicate(text, self.voice, **kwargs)
        size = 0
        try:
            with open(mp3, "wb") as f:
                async for chunk in comm.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                        size += len(chunk["data"])
            if size < 2000:
                raise RuntimeError(f"audio too small ({size} bytes)")
            await asyncio.to_thread(self._mp3_to_wav, mp3, path)
        finally:
            try:
                if os.path.exists(mp3):
                    os.remove(mp3)
            except OSError:
                pass
        return size

    @staticmethod
    def _mp3_to_wav(mp3, wav):
        subprocess.run(
            [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", mp3,
             "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
             "-sample_fmt", "s16", wav],
            check=True,
        )

    async def _attempt(self, text, path, mood):
        """尝试合成一条（内部重试 + 退避），失败时抛异常，不做静音兜底。"""
        key = self._cache_key(text, mood)
        wav_c, meta_c = self._cache_paths(path, key)
        cached = self._cache_load(wav_c, path)
        if cached is not None:
            return cached
        if self.offline:
            raise RuntimeError("网络已被标记不可达（熔断），跳过网络合成")
        last = None
        for attempt in range(1, self.retries + 1):
            try:
                await self._fetch(text, path, mood)
                dur = await asyncio.to_thread(_trim, path)
                self._cache_store(path, dur, wav_c, meta_c)
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
                    except Exception as e:  # noqa: BLE001  单项失败不拖累其余
                        errors[i] = e

            await asyncio.gather(*(one(i) for i in pending))
            if p + 1 < self.retry_passes and any(durs[i] is None for i in range(n)):
                await asyncio.sleep(0.5)
        for i in range(n):
            if durs[i] is None:
                self.failures += 1
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
      name / rate / pitch / volume: 音色与基础韵律
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
    try:
        return EdgeVoice(
            cfg.get("name", "zh-CN-XiaoxiaoNeural"),
            rate=cfg.get("rate", "+0%"),
            pitch=cfg.get("pitch", "+0Hz"),
            volume=cfg.get("volume", "+0%"),
            retries=cfg.get("retries", 4),
            retry_delay=cfg.get("retry_delay", 0.8),
            retry_passes=cfg.get("retry_passes", 2),
            proxy=cfg.get("proxy", "auto"),
            cache=cache,
            cache_dir=cache_dir,
            jobs=cfg.get("jobs") or cfg.get("parallel"),
            verbose=cfg.get("verbose", True),
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
