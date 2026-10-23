"""What a given encoder lowpass usually means, and how good that makes the audio.

Encoders pick their lowpass from the bitrate, so the cutoff is a fingerprint of the
bitrate the audio was once encoded at. LAME_MEASURED was calibrated by encoding real
music with LAME 3.100 (via ffmpeg) at every common setting and measuring the result
with find_cutoff(). Other encoders and versions differ by a few hundred Hz.
"""

from __future__ import annotations

# (measured cutoff Hz, LAME settings that produce it, quality score where 320 kbps CBR = 100)
LAME_MEASURED = [
    (20230, "320 kbps CBR or VBR V0", 100),
    (19560, "224-256 kbps CBR or VBR V1", 90),
    (18860, "192 kbps CBR or VBR V2", 80),
    (18170, "~180 kbps (VBR V3)", 72),
    (17480, "160 kbps CBR or VBR V4", 65),
    (16800, "128 kbps CBR or VBR V5-V6", 55),
    (15440, "96-112 kbps CBR or VBR V7", 40),
    (13500, "~80 kbps", 30),
    (11000, "~64 kbps", 20),
    (7500, "48 kbps or lower", 10),
]
SNAP_HZ = 250  # measured cutoffs land within ~100 Hz of the table; allow other encoder versions

# LAME 3.100 CBR/ABR lowpass setting (Hz) by bitrate (kbps), 44.1 kHz stereo
LAME_LOWPASS = {
    32: 5500, 40: 7000, 48: 7500, 56: 10000, 64: 11000, 80: 13500, 96: 15100,
    112: 15600, 128: 17000, 160: 17500, 192: 18600, 224: 19400, 256: 19700, 320: 20500,
}

OTHER_CODECS = [
    (15400, 16500, "the classic 16 kHz lowpass of older LAME/Fraunhofer 128 kbps encoders, or ~96-128 kbps AAC"),
    (16500, 17800, "~128 kbps AAC"),
    (17800, 19000, "~112-160 kbps Vorbis"),
    (19000, 19800, "~192-256 kbps AAC/Vorbis"),
    (19800, 21000, "Opus (20 kHz band limit) or ~256-320 kbps AAC"),
]


def quality_for_cutoff(freq_hz: float) -> tuple[int, str]:
    """Quality score (320 kbps MP3 = 100) and the MP3 settings this cutoff matches."""
    table = sorted(LAME_MEASURED)  # ascending cutoff
    for hz, label, score in table:
        if abs(freq_hz - hz) <= SNAP_HZ:
            return score, label
    if freq_hz >= table[-1][0]:
        return 100, table[-1][1]
    if freq_hz <= table[0][0]:
        return table[0][2], table[0][1]
    # between two settings: interpolate the score, name the neighbours
    for (lo_hz, lo_label, lo_score), (hi_hz, hi_label, hi_score) in zip(table, table[1:]):
        if lo_hz < freq_hz < hi_hz:
            t = (freq_hz - lo_hz) / (hi_hz - lo_hz)
            return round(lo_score + t * (hi_score - lo_score)), f"between {lo_label} and {hi_label}"
    return 0, "unknown"


def describe_cutoff(freq_hz: float) -> str:
    _, label = quality_for_cutoff(freq_hz)
    other = next((text for lo, hi, text in OTHER_CODECS if lo <= freq_hz < hi), None)
    return f"MP3 at {label}" + (f"; or {other}" if other else "")


def lame_bitrate_for_cutoff(freq_hz: float) -> int:
    """The LAME CBR bitrate whose lowpass is closest to this cutoff."""
    return min(LAME_LOWPASS, key=lambda kbps: abs(LAME_LOWPASS[kbps] - freq_hz))


def expected_min_cutoff(codec: str, kbps: float | None) -> float | None:
    """Lowest cutoff we'd expect from a file honestly encoded at its own bitrate."""
    if not kbps:
        return None
    if codec in ("MPEG_LAYER_III", "mp3"):
        rates = [r for r in LAME_LOWPASS if r <= kbps * 1.05]
        # allow for VBR and older encoders, which cut ~1 kHz lower than LAME 3.100 CBR
        return LAME_LOWPASS[max(rates)] - 1200 if rates else None
    if kbps >= 192:
        return 18000
    if kbps >= 128:
        return 15000
    return None
