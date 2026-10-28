"""配音层（WSL/Linux 版）：edge-tts 神经音色 + mood 情绪预设 + 静音兜底。

与 Windows 版差异：
  - 无 SAPI，断网直接降级静音占位（时长按字数估算，流水线不中断）
  - mp3 -> wav 用 imageio-ffmpeg 内置 ffmpeg
"""
import os
import struct
import subprocess
import wave

SAMPLE_RATE = 22050
CHANNELS = 1
SAMPWIDTH = 2

MOODS = {
    "hook":    dict(rate="+12%", pitch="+3Hz", volume="+0%"),
    "rule":    dict(rate="+2%",  pitch="+0Hz", volume="+0%"),
    "explain": dict(rate="-4%",  pitch="-1Hz", volume="+0%"),
    "reveal":  dict(rate="+6%",  pitch="+2Hz", volume="+6%"),
    "win":     dict(rate="+10%", pitch="+2Hz", volume="+8%"),
}


def ffmpeg_exe():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def _trim(path, thresh=420, pad=0.06):
    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
    samples = struct.unpack("<%dh" % (len(raw) // 2), raw)
    head = next((i for i, s in enumerate(samples) if abs(s) > thresh), 0)
    tail = len(samples) - next(
        (i for i, s in enumerate(reversed(samples)) if abs(s) > thresh), 0)
    p = int(SAMPLE_RATE * pad)
    head, tail = max(0, head - p), min(len(samples), tail + p)
    kept = samples[head:tail]
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPWIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack("<%dh" % len(kept), *kept))
    return len(kept) / SAMPLE_RATE


def _silence(path, seconds):
    with wave.open(path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPWIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(b"\x00\x00" * int(SAMPLE_RATE * seconds))
    return seconds


class EdgeVoice:
    def __init__(self, voice="zh-CN-XiaoxiaoNeural", rate="+0%", pitch="+0Hz", retries=3):
        import edge_tts
        self._edge = edge_tts
        self.voice, self.rate, self.pitch, self.retries = voice, rate, pitch, retries
        self.name = f"edge:{voice}"

    def synth(self, text, path, mood=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pros = MOODS.get(mood, {})
        mp3 = path + ".mp3"
        last = None
        for _ in range(self.retries):
            try:
                import asyncio

                c = self._edge.Communicate(
                    text, self.voice,
                    rate=pros.get("rate", self.rate),
                    pitch=pros.get("pitch", self.pitch),
                    volume=pros.get("volume", "+0%"),
                )

                async def run():
                    with open(mp3, "wb") as f:
                        async for chunk in c.stream():
                            if chunk["type"] == "audio":
                                f.write(chunk["data"])

                asyncio.run(run())
                if os.path.getsize(mp3) < 2000:
                    raise RuntimeError("audio too small")
                subprocess.run(
                    [ffmpeg_exe(), "-y", "-loglevel", "error", "-i", mp3,
                     "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
                     "-sample_fmt", "s16", path],
                    check=True,
                )
                os.remove(mp3)
                return _trim(path)
            except Exception as e:  # noqa: BLE001
                last = e
        raise RuntimeError(f"edge-tts failed: {last}")


class SilentVoice:
    def __init__(self):
        self.name = "silent"

    def synth(self, text, path, mood=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return _silence(path, max(2.0, len(text) / 6.2))


def build_voice(cfg):
    try:
        return EdgeVoice(cfg.get("name", "zh-CN-XiaoxiaoNeural"),
                         rate=cfg.get("rate", "+0%"),
                         pitch=cfg.get("pitch", "+0Hz"))
    except Exception as e:  # noqa: BLE001
        print(f"[voice] edge 不可用（{e}），降级静音（字幕仍正常）")
        return SilentVoice()


def concat(segments, total, out_path):
    track = [0] * int(SAMPLE_RATE * total)
    for start, path in segments:
        with wave.open(path, "rb") as w:
            raw = w.readframes(w.getnframes())
        s = struct.unpack("<%dh" % (len(raw) // 2), raw)
        off = int(start * SAMPLE_RATE)
        end = min(len(track), off + len(s))
        track[off:end] = list(s[: end - off])
    with wave.open(out_path, "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(SAMPWIDTH)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(struct.pack("<%dh" % len(track), *track))
    return out_path
