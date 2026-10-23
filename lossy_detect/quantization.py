"""Sample-value evidence: real bit depth, missing codes, clipping.

These don't prove lossy coding on their own, but they expose upconversion
(16-bit audio padded into a 24-bit container, gain applied after quantization)
and give the noise floor the spectral tests compare against.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class SampleStats:
    container_bits: int | None
    is_float: bool
    effective_bits: int | None      # bits actually used (None = full float precision)
    padded: bool                    # container has more bits than are used
    missing_code_fraction: float | None  # unused codes in a region where every code should appear
    clipped_samples: int
    clip_runs: int                  # runs of >= 3 consecutive full-scale samples
    peak_dbfs: float

    @property
    def analysis_bits(self) -> int:
        """Resolution to assume for noise-floor calculations."""
        return min(self.effective_bits or 24, 24)


def _effective_int_bits(ints: np.ndarray, container_bits: int) -> int:
    """Container bits minus the trailing bits that are zero in every sample."""
    used = int(np.bitwise_or.reduce(np.abs(ints.astype(np.int64)).ravel()))
    if used == 0:
        return 0
    shift = 32 - container_bits
    used >>= shift
    trailing = (used & -used).bit_length() - 1
    return container_bits - trailing


def _effective_float_bits(x: np.ndarray) -> int | None:
    """Smallest integer resolution that represents every float sample exactly."""
    sample = x.ravel()[: 2_000_000]
    for bits in (8, 16, 20, 24):
        scaled = sample * 2.0 ** (bits - 1)
        if np.array_equal(scaled, np.round(scaled)):
            return bits
    return None


def _missing_codes(codes: np.ndarray) -> float | None:
    """Fraction of empty histogram codes in the range where the histogram is dense.

    Near zero, real audio quantized at this resolution uses every code. Gaps in a
    regular pattern mean digital gain was applied after an earlier, coarser
    quantization (e.g. 16-bit audio normalised and saved as 24-bit).
    """
    limit = 1 << 15
    c = codes[np.abs(codes) < limit]
    if len(c) < 10_000:
        return None
    hist = np.bincount(c + limit, minlength=2 * limit)
    smooth = np.convolve(hist, np.ones(65) / 65, "same")
    dense = smooth >= 20
    if dense.sum() < 64:
        return None
    return float(np.mean(hist[dense] == 0))


def analyse(samples: np.ndarray, int_samples: np.ndarray | None, container_bits: int | None, is_float: bool) -> SampleStats:
    if int_samples is not None and container_bits:
        eff = _effective_int_bits(int_samples, container_bits)
        codes = (int_samples >> (32 - max(eff, 1))).ravel()
        missing = _missing_codes(codes)
    elif is_float:
        eff = _effective_float_bits(samples)
        missing = _missing_codes(np.round(samples * 2.0 ** (eff - 1)).astype(np.int64).ravel()) if eff else None
    else:  # decoded lossy stream: no meaningful word length
        eff, missing = None, None

    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    full = np.abs(samples) >= min(peak, 1.0) - 2.0 ** -15 if peak > 0.98 else np.zeros(samples.shape, bool)
    runs = 0
    for ch in range(samples.shape[1]):
        f = np.concatenate([[0], full[:, ch].astype(np.int8), [0]])
        starts, ends = np.nonzero(np.diff(f) == 1)[0], np.nonzero(np.diff(f) == -1)[0]
        runs += int(np.sum(ends - starts >= 3))

    return SampleStats(
        container_bits=container_bits,
        is_float=is_float,
        effective_bits=eff,
        padded=bool(container_bits and eff and eff < container_bits),
        missing_code_fraction=missing,
        clipped_samples=int(full.sum()),
        clip_runs=runs,
        peak_dbfs=float(20 * np.log10(max(peak, 1e-12))),
    )
