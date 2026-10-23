"""Time-domain structural evidence: the coding-frame grid.

A transform codec quantizes MDCT coefficients block by block. When the decoded
audio is re-analysed with an MDCT of the same block size and window *at the same
alignment*, the coefficients the encoder quantized to zero come back as (near)
zeros; at any other alignment the energy smears across them. Scanning every
offset within a block therefore gives a sharp spike at exactly one offset for
previously coded audio, and a flat line for audio that never went through a codec.

Three statistics are computed per offset; a genuine grid makes at least two of
them peak at the same offset, while random fluctuations do not line up.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import scipy.fft as sfft
from scipy.signal.windows import kaiser

# (half block length N, window, label)
CANDIDATES = [
    (1024, "kbd", "AAC (1024-sample MDCT, KBD window)"),
    (1024, "sine", "AAC (1024-sample MDCT, sine window)"),
    (1024, "vorbis", "Vorbis (2048-sample blocks)"),
    (576, "sine", "MP3 (576-sample granules)"),
    (960, "celt", "Opus (CELT 960-sample frames)"),  # only at 48 kHz, Opus's native rate
]


@dataclass
class GridResult:
    block: int
    window: str
    label: str
    offset: int
    zscore: float          # strongest of the three statistics, robust z vs. all other offsets
    agreeing_stats: int    # how many statistics peak at (nearly) the same offset with z >= 6
    curve: np.ndarray      # the log-magnitude statistic per offset, for plotting

    @property
    def level(self) -> str:
        if self.agreeing_stats >= 2 and self.zscore >= 15:
            return "strong"
        if self.agreeing_stats >= 2 and self.zscore >= 7:
            return "moderate"
        return "none"


def _window(n: int, kind: str) -> np.ndarray:
    t = (np.arange(2 * n) + 0.5) / (2 * n)
    if kind == "sine":
        return np.sin(np.pi * t)
    if kind == "vorbis":
        return np.sin(np.pi / 2 * np.sin(np.pi * t) ** 2)
    if kind == "celt":
        # CELT: short Vorbis-shaped overlap (120 of 960 samples), flat in the middle
        ov = 120 * n // 960
        rise = np.sin(np.pi / 2 * np.sin(np.pi * (np.arange(ov) + 0.5) / (2 * ov)) ** 2)
        pad = np.zeros((n - ov) // 2)
        return np.concatenate([pad, rise, np.ones(n - ov), rise[::-1], pad])
    k = kaiser(n + 1, np.pi * 4)  # KBD, alpha = 4 (AAC long window)
    c = np.cumsum(k[:n])
    return np.sqrt(np.concatenate([c, c[::-1]]) / k.sum())


def mdct(frames: np.ndarray) -> np.ndarray:
    """MDCT of windowed frames (rows of length 2N) via TDAC folding + DCT-IV."""
    n = frames.shape[1] // 2
    h = n // 2
    a, b, c, d = frames[:, :h], frames[:, h:n], frames[:, n:n + h], frames[:, n + h:]
    folded = np.concatenate([-c[:, ::-1] - d, a - b[:, ::-1]], axis=1)
    return sfft.dct(folded, type=4, axis=1, workers=-1) / 2


def pick_segment(x: np.ndarray, sr: int, seconds: float) -> np.ndarray:
    """The loudest `seconds` of the signal, where coded coefficients are densest."""
    length = int(seconds * sr)
    if len(x) <= length:
        return x
    hop = sr // 4
    energy = np.add.reduceat(x**2, np.arange(0, len(x), hop))
    blocks = int(seconds * 4)
    start = int(np.argmax(np.convolve(energy, np.ones(blocks), "valid"))) * hop
    return x[start:start + length]


def _robust_z(curve: np.ndarray) -> tuple[int, float]:
    med = np.median(curve)
    # floor keeps z finite when a statistic is flat (e.g. no near-zero coefficients anywhere)
    mad = max(np.median(np.abs(curve - med)) * 1.4826, 1e-4)
    best = int(np.argmax(curve))
    return best, float((curve[best] - med) / mad)


def scan(x: np.ndarray, sr: int, n: int, window: str, top_hz: float, quant_step: float, label: str) -> GridResult:
    frames = max(8, min(int(2.5 * sr / n), (len(x) - 3 * n) // n))
    win = _window(n, window).astype(np.float32)
    lo = int(1500 / (sr / 2) * n)
    hi = max(lo + 8, int(min(top_hz, sr / 2) / (sr / 2) * n))
    # Rounding noise of the stored samples maps to coefficient std ~ q/sqrt(12) * sqrt(N/2)
    base = quant_step / np.sqrt(12) * np.sqrt(n / 2)
    idx = np.arange(2 * n)[None, :] + n * np.arange(frames)[:, None]
    x32 = x.astype(np.float32)

    stats = np.empty((3, n))
    for off in range(n):
        mag = np.abs(mdct(x32[off + idx] * win)[:, lo:hi])
        stats[0, off] = np.mean(mag < 3 * base)
        stats[1, off] = np.mean(mag < 30 * base)
        stats[2, off] = -np.mean(np.log10(mag + base))

    peaks = [_robust_z(s) for s in stats]
    best_off, best_z = max(peaks, key=lambda p: p[1])
    agree = sum(1 for off, z in peaks if z >= 6 and min(abs(off - best_off), n - abs(off - best_off)) <= 2)
    return GridResult(n, window, label, best_off, best_z, agree, stats[2])


def detect_grids(samples: np.ndarray, sr: int, top_hz: float, quant_step: float, seconds: float = 3.0,
                 on_candidate: Callable[[str, int, int], None] | None = None) -> list[GridResult]:
    """Scan each candidate codec grid on the left channel; strongest result first.

    on_candidate(label, index, total) is called before each scan (for progress display).
    """
    seg = pick_segment(samples[:, 0], sr, seconds)
    if len(seg) < 4 * 2048:
        return []
    todo = [c for c in CANDIDATES if c[1] != "celt" or sr == 48000]
    results = []
    for i, (n, w, label) in enumerate(todo):
        if on_candidate:
            on_candidate(label, i, len(todo))
        results.append(scan(seg, sr, n, w, top_hz, quant_step, label))
    return sorted(results, key=lambda r: (r.level != "none", r.agreeing_stats >= 2, r.zscore), reverse=True)
