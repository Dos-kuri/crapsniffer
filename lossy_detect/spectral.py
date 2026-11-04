"""Frequency-domain evidence of lossy coding.

Perceptual codecs (MP3, AAC, Vorbis, Opus...) leave several marks in the spectrum:

* a hard lowpass ("brickwall") set by the encoder for the chosen bitrate, which
  every frame respects regardless of how bright the music is;
* scale-factor bands quantized to zero, which show up as rectangular holes in the
  spectrogram well below the cutoff;
* high bands that switch on and off from one coding frame to the next (LAME's
  sfb21 behaviour, AAC/Vorbis band dropping) instead of decaying naturally;
* joint stereo: the side (L-R) channel band-limited harder than the mid channel.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.ndimage import uniform_filter1d

EPS = 1e-30


def db(x):
    return 10.0 * np.log10(np.maximum(x, EPS))


def stft_power(x: np.ndarray, nfft: int, hop: int) -> np.ndarray:
    """Power spectrogram, shape (frames, nfft//2+1), Hann window, float32."""
    if len(x) < nfft:
        x = np.pad(x, (0, nfft - len(x)))
    # Strided view of just the frames used. (sliding_window_view(x, nfft)[::hop] gives the same
    # frames, but first describes every possible window: too big to address in the browser's 32-bit memory.)
    x = np.ascontiguousarray(x)
    n_frames = (len(x) - nfft) // hop + 1
    frames = np.lib.stride_tricks.as_strided(x, shape=(n_frames, nfft), strides=(x.strides[0] * hop, x.strides[0]),
                                             writeable=False)
    win = np.hanning(nfft).astype(np.float32)
    out = np.empty((frames.shape[0], nfft // 2 + 1), dtype=np.float32)
    for i in range(0, frames.shape[0], 512):  # chunked to bound memory
        spec = np.fft.rfft(frames[i:i + 512].astype(np.float32) * win, axis=1)
        out[i:i + 512] = spec.real**2 + spec.imag**2
    return out


def long_nfft(sr: int) -> int:
    """FFT size giving ~5 Hz resolution at any sample rate."""
    return int(2 ** np.round(np.log2(sr / 5.4)))


def rounding_noise_var(bits: int) -> float:
    """Variance of plain rounding noise at `bits` resolution (full scale = 1.0)."""
    q = 2.0 ** -(bits - 1)
    return q * q / 12.0


def bin_power(noise_var: float, nfft: int) -> float:
    """Expected per-bin power of white noise with variance noise_var (Hann window)."""
    return noise_var * float(np.sum(np.hanning(nfft) ** 2))


def active_frames(power: np.ndarray, rel_db: float = 40.0) -> np.ndarray:
    """Frames loud enough to analyse: within rel_db of the loudest frame and above -70 dBFS-ish."""
    level = db(power.sum(axis=1))
    return level > level.max() - rel_db


# --------------------------------------------------------------------------- cutoff

@dataclass
class Cutoff:
    found: bool
    freq_hz: float | None = None
    drop_db: float = 0.0            # level just below the edge minus the loudest level anywhere above it
    floor_above_db: float | None = None   # median level above the edge, dB per bin
    transition_hz: float | None = None    # width of the -3 dB..-(drop-3) dB transition
    frames_at_ceiling: float | None = None  # fraction of loud frames with strong content right up to the edge
    frames_exceeding: float | None = None   # ...of those, the fraction that also carry content past it


def find_cutoff(freqs: np.ndarray, spec_db: np.ndarray, sr: int,
                min_freq: float = 2500.0, below_hz: float = 600.0, gap_hz: float = 120.0) -> Cutoff:
    """Locate the frequency where the long-term spectrum falls off a cliff.

    For every candidate edge f we compare the mean level in [f-below_hz, f-gap_hz]
    with the 95th-percentile level of everything above f+gap_hz. A natural rolloff
    gives a few dB; an encoder lowpass gives 25-70 dB.
    """
    df = freqs[1] - freqs[0]
    nyq = sr / 2
    smooth = uniform_filter1d(spec_db, max(1, int(150 / df)))
    n_below, n_gap = int(below_hz / df), int(gap_hz / df)
    top = int(nyq * 0.995 / df)
    lo = max(int(min_freq / df), n_below)

    # 95th percentile of everything above each bin, computed from the top down
    above_p95 = np.full(len(smooth), -np.inf)
    tail = smooth[:top]
    for i in range(lo, top - n_gap - 10, max(1, int(25 / df))):
        above_p95[i] = np.percentile(tail[i + n_gap:], 95)
    csum = np.concatenate([[0.0], np.cumsum(smooth)])
    idx = np.arange(len(smooth))
    below_mean = np.full(len(smooth), -np.inf)
    valid = (idx >= lo) & np.isfinite(above_p95)
    i = idx[valid]
    below_mean[valid] = (csum[i - n_gap] - csum[i - n_below]) / (n_below - n_gap)
    with np.errstate(invalid="ignore"):
        drop = below_mean - above_p95
    drop[~np.isfinite(drop)] = -np.inf

    best = int(np.argmax(drop))
    max_drop = float(drop[best])
    if max_drop < 10:
        return Cutoff(found=False, drop_db=max(max_drop, 0.0))
    # The edge is the highest candidate that still achieves (nearly) the full drop
    edge = int(np.nonzero(drop >= max_drop - 3)[0].max())
    # Snap to where the unsmoothed spectrum actually crosses half-way down the cliff
    target = below_mean[edge] - max_drop / 2
    window = spec_db[edge - n_gap: edge + 4 * n_gap]
    crossing = np.nonzero(uniform_filter1d(window, max(1, int(40 / df))) < target)[0]
    edge_hz = freqs[edge - n_gap + crossing[0]] if len(crossing) else freqs[edge]

    hi = smooth[edge - n_below: min(top, edge + n_below * 2)]
    f_hi = freqs[edge - n_below: min(top, edge + n_below * 2)]
    upper, lower = below_mean[edge] - 3, below_mean[edge] - max_drop + 3
    in_band = f_hi[(hi < upper) & (hi > lower)]
    transition = float(in_band.max() - in_band.min()) if len(in_band) > 1 else float(df)

    return Cutoff(
        found=True,
        freq_hz=float(edge_hz),
        drop_db=max_drop,
        floor_above_db=float(np.median(spec_db[int(edge_hz / df) + n_gap: top])),
        transition_hz=transition,
    )


def cutoff_consistency(power: np.ndarray, freqs: np.ndarray, cut: Cutoff) -> None:
    """Fill cut.frames_at_ceiling / cut.frames_exceeding from per-frame spectra.

    Only frames with strong content just below the cutoff (35 dB over the floor)
    count. Such a frame "exceeds" the cutoff if, just past the cliff, it is still
    15 dB over the floor and within 20 dB of its own level below the cliff -
    which natural rolloff is and an encoder lowpass never is.
    """
    df = freqs[1] - freqs[0]
    past_cliff = cut.freq_hz + cut.transition_hz + 200
    below = (freqs > cut.freq_hz - 1000) & (freqs < cut.freq_hz - 200)
    past = (freqs > past_cliff) & (freqs < past_cliff + 2300)
    if not below.any() or not past.any():
        return
    cols = below | past
    sm = db(uniform_filter1d(power[:, cols], max(1, int(200 / df)), axis=1))
    lvl_below = sm[:, below[cols]].mean(axis=1)
    lvl_past = sm[:, past[cols]].mean(axis=1)
    near = lvl_below > cut.floor_above_db + 35
    cut.frames_at_ceiling = float(near.mean())
    if near.sum() >= 5:
        exceeds = (lvl_past > lvl_below - 20) & (lvl_past > cut.floor_above_db + 15)
        cut.frames_exceeding = float(np.mean(exceeds[near]))


# --------------------------------------------------------------------------- holes & dropouts

@dataclass
class HoleStats:
    band_hz: tuple[float, float]
    hole_fraction: float          # fraction of loud time-frequency cells sitting at the noise floor
    expected_fraction: float      # what Rayleigh statistics predict for an un-coded signal
    dropout_rate: float           # fraction of frame transitions where the top band switches on/off
    dropout_frames: float         # fraction of frames where the top band is at the floor while mids are loud
    details: dict = field(default_factory=dict)


def holes_and_dropouts(mono: np.ndarray, sr: int, top_hz: float, noise_var: float) -> HoleStats:
    """Short-window analysis of the band just below the cutoff (or below Nyquist).

    Uses ~23 ms frames (roughly one MP3 granule pair / one AAC frame) so per-frame
    band switching is resolved. noise_var is the white-noise floor of the file
    (rounding noise, or whatever is measured above the cutoff).
    """
    nfft = int(2 ** np.round(np.log2(sr * 0.023)))
    hop = nfft // 2
    power = stft_power(mono, nfft, hop)
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    df = freqs[1] - freqs[0]
    floor_bin = bin_power(noise_var, nfft)

    band_lo = max(4000.0, top_hz * 0.55)
    band = (freqs >= band_lo) & (freqs < top_hz - 2 * df)
    ref = (freqs >= 1500) & (freqs < 5000)
    top = (freqs >= max(band_lo, top_hz - 2500)) & (freqs < top_hz - 2 * df)

    loud = active_frames(power, 35.0)
    p_band = power[loud][:, band]

    # Holes: cells >= 22 dB under the frame's own band median and within 4 dB of the floor
    med = np.median(p_band, axis=1, keepdims=True)
    lively = (db(med) > db(floor_bin) + 26).ravel()   # only frames where the band is clearly in use
    if lively.sum() >= 10:
        sub = p_band[lively]
        m = med[lively]
        hole = (sub < m * 10 ** (-22 / 10)) & (sub < floor_bin * 10 ** (4 / 10))
        hole_fraction = float(hole.mean())
        # For a noise-like (chi-square, 2 dof) bin: P(power < m * 10^-2.2) = 1 - 2^(-10^-2.2)
        expected = float(1 - 2.0 ** (-(10 ** -2.2)))
    else:
        hole_fraction, expected = 0.0, 0.0

    # Dropouts: top band energy relative to the reference band, frame to frame
    e_top = db(power[:, top].sum(axis=1))
    e_ref = db(power[:, ref].sum(axis=1))
    floor_top = db(floor_bin * top.sum())
    usable = loud & (e_ref > db(floor_bin * ref.sum()) + 40)
    on = e_top > floor_top + 18
    off = e_top < floor_top + 5
    pairs = usable[1:] & usable[:-1] & (np.abs(np.diff(e_ref)) < 4)
    switches = pairs & ((on[1:] & off[:-1]) | (off[1:] & on[:-1]))
    # Count only when the band is sometimes on: a band that is always off is just the cutoff
    on_fraction = float(on[usable].mean()) if usable.any() else 0.0
    dropout_rate = float(switches.sum() / max(1, pairs.sum())) if 0.05 < on_fraction < 0.98 else 0.0
    dropout_frames = float((off & usable).sum() / max(1, usable.sum())) if 0.05 < on_fraction else 0.0

    return HoleStats(
        band_hz=(float(band_lo), float(top_hz)),
        hole_fraction=hole_fraction,
        expected_fraction=expected,
        dropout_rate=dropout_rate,
        dropout_frames=dropout_frames,
        details={"top_band_on_fraction": on_fraction, "frames_analysed": int(usable.sum())},
    )


# --------------------------------------------------------------------------- stereo

@dataclass
class StereoStats:
    applicable: bool
    side_to_mid_db: float | None = None          # overall side level relative to mid
    mid_cutoff_hz: float | None = None
    side_cutoff_hz: float | None = None
    side_drop_db: float | None = None            # how much S/M falls in the top band vs. the mid band
    ratio_curve: tuple[np.ndarray, np.ndarray] | None = None


def stereo_analysis(samples: np.ndarray, sr: int) -> StereoStats:
    if samples.shape[1] < 2:
        return StereoStats(applicable=False)
    mid = (samples[:, 0] + samples[:, 1]) / 2
    side = (samples[:, 0] - samples[:, 1]) / 2
    nfft = long_nfft(sr) // 2
    pm, ps = stft_power(mid, nfft, nfft), stft_power(side, nfft, nfft)
    act = active_frames(pm)
    m, s = pm[act].mean(axis=0), ps[act].mean(axis=0)
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    s_to_m = float(db(s.sum()) - db(m.sum()))
    if s_to_m < -45:
        return StereoStats(applicable=False, side_to_mid_db=s_to_m)

    cm = find_cutoff(freqs, db(m), sr)
    cs = find_cutoff(freqs, db(s), sr)
    top_hz = cm.freq_hz if cm.found else min(19000.0, sr * 0.45)
    df = freqs[1] - freqs[0]
    ratio = db(uniform_filter1d(s, int(300 / df))) - db(uniform_filter1d(m, int(300 / df)))
    mid_band = (freqs > 1000) & (freqs < 6000)
    top_band = (freqs > top_hz - 3000) & (freqs < top_hz - 300)
    drop = float(np.median(ratio[mid_band]) - np.median(ratio[top_band])) if top_band.any() else None
    return StereoStats(
        applicable=True,
        side_to_mid_db=s_to_m,
        mid_cutoff_hz=cm.freq_hz if cm.found and cm.drop_db >= 20 else None,
        side_cutoff_hz=cs.freq_hz if cs.found and cs.drop_db >= 20 else None,
        side_drop_db=drop,
        ratio_curve=(freqs, ratio),
    )
