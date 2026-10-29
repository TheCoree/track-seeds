"""Звук: WAV ↔ numpy, передискретизация, кодирование G.711 (µ-law/A-law) для динамиков камер."""

import io
import wave

import numpy as np


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """WAV (8/16/32 бит PCM) → моно float32 в [-1, 1] и частота."""
    with wave.open(io.BytesIO(data)) as wav:
        rate, channels, width = wav.getframerate(), wav.getnchannels(), wav.getsampwidth()
        raw = wav.readframes(wav.getnframes())
    match width:
        case 1:
            samples = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128) / 128
        case 2:
            samples = np.frombuffer(raw, "<i2").astype(np.float32) / 32768
        case 4:
            samples = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648
        case _:
            raise ValueError(f"WAV с {width * 8} бит на отсчёт не поддерживается")
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, rate


def to_pcm16(samples: np.ndarray) -> np.ndarray:
    return (np.clip(samples, -1, 1) * 32767).astype("<i2")


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(to_pcm16(samples).tobytes())
    return buf.getvalue()


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Передискретизация с фильтром нижних частот (windowed sinc) — без scipy, для речи хватает."""
    if src_rate == dst_rate or samples.size == 0:
        return samples.astype(np.float32)
    if dst_rate < src_rate:
        cutoff = 0.45 * dst_rate / src_rate  # доля частоты дискретизации исходника, с запасом до Найквиста
        taps = np.arange(-64, 65)
        kernel = 2 * cutoff * np.sinc(2 * cutoff * taps) * np.hamming(taps.size)
        samples = np.convolve(samples, kernel / kernel.sum(), mode="same")
    duration = samples.size / src_rate
    positions = np.arange(int(duration * dst_rate)) * src_rate / dst_rate
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


def normalize(samples: np.ndarray, peak: float = 0.9) -> np.ndarray:
    top = float(np.max(np.abs(samples))) if samples.size else 0.0
    return samples * (peak / top) if top > 0 else samples


def ulaw_encode(samples: np.ndarray) -> bytes:
    """G.711 µ-law (классический алгоритм из g711.c Sun Microsystems)."""
    pcm = to_pcm16(samples).astype(np.int32)
    sign = np.where(pcm < 0, 0x80, 0)
    magnitude = np.minimum(np.abs(pcm), 32635) + 0x84
    exponent = np.clip(np.floor(np.log2(magnitude >> 7)).astype(np.int32), 0, 7)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8).tobytes()


def alaw_encode(samples: np.ndarray) -> bytes:
    """G.711 A-law (классический алгоритм из g711.c Sun Microsystems)."""
    pcm = to_pcm16(samples).astype(np.int32) >> 3
    mask = np.where(pcm >= 0, 0xD5, 0x55)
    pcm = np.where(pcm >= 0, pcm, -pcm - 1)
    segment = np.searchsorted(np.array([0x1F, 0x3F, 0x7F, 0xFF, 0x1FF, 0x3FF, 0x7FF, 0xFFF]), pcm)
    value = (segment << 4) | np.where(segment < 2, pcm >> 1, pcm >> np.maximum(segment, 1)) & 0x0F
    value = np.where(segment >= 8, 0x7F, value)
    return ((value ^ mask) & 0xFF).astype(np.uint8).tobytes()


def chime(rate: int) -> np.ndarray:
    """Короткий двухтональный сигнал «внимание» перед фразой."""
    parts = []
    for freq in (880, 660):
        t = np.arange(int(0.22 * rate)) / rate
        tone = np.sin(2 * np.pi * freq * t) * np.minimum(1, np.minimum(t, t[-1] - t) / 0.02)
        parts += [0.6 * tone, np.zeros(int(0.06 * rate))]
    return np.concatenate(parts + [np.zeros(int(0.25 * rate))]).astype(np.float32)
