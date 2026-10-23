"""Run every detector on a file and weigh the evidence into a verdict."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import audio_io, bitrate, quantization, spectral, structure

LEVEL_ORDER = {"strong": 0, "moderate": 1, "weak": 2, "info": 3, "none": 4}


@dataclass
class Finding:
    title: str
    level: str      # strong | moderate | weak | none | info
    points: int
    text: str


@dataclass
class Report:
    audio: audio_io.Audio
    samples: quantization.SampleStats
    cutoff: spectral.Cutoff
    holes: spectral.HoleStats
    stereo: spectral.StereoStats
    grids: list[structure.GridResult]
    findings: list[Finding] = field(default_factory=list)
    upconversion: list[str] = field(default_factory=list)
    score: int = 0
    verdict: str = ""
    quality: int = 100            # 320 kbps MP3 = 100; see bitrate.LAME_MEASURED
    quality_label: str = ""
    source_guess: str | None = None
    # kept for plotting
    freqs: np.ndarray | None = None
    spectrum_db: np.ndarray | None = None
    spectrogram_db: np.ndarray | None = None
    spectrogram_hop_s: float = 0.0
    noise_floor_db: float | None = None


def _codec_family(codec: str) -> str | None:
    c = codec.lower()
    if "layer_iii" in c or c == "mp3":
        return "MP3"
    if c.startswith("aac"):
        return "AAC"
    if "vorbis" in c:
        return "Vorbis"
    if "opus" in c:
        return "Opus"
    return None


def analyse_file(path: str, grid_scan: bool = True, progress: Callable[[str, float], None] | None = None) -> Report:
    """progress(stage, fraction_done) is called as each step starts (used by the app)."""
    step = progress or (lambda stage, done: None)
    step("Decoding audio", 0.0)
    audio = audio_io.load(path)
    sr = audio.sample_rate
    nyq = sr / 2
    stats = quantization.analyse(audio.samples, audio.int_samples, audio.container_bits, audio.is_float)
    mono = audio.samples.mean(axis=1)

    step("Measuring the frequency spectrum", 0.15)
    # Long-window spectrum: ~5 Hz resolution, non-overlapping frames
    nfft = spectral.long_nfft(sr)
    power = spectral.stft_power(mono, nfft, nfft)
    act = spectral.active_frames(power)
    freqs = np.fft.rfftfreq(nfft, 1 / sr)
    spec_db = spectral.db(power[act].mean(axis=0))
    cut = spectral.find_cutoff(freqs, spec_db, sr)
    hard = cut.found and cut.drop_db >= 25

    if cut.found and cut.drop_db >= 15:
        spectral.cutoff_consistency(power[act], freqs, cut)

    # Noise floor: what's measured above a real cutoff, else the file's rounding noise
    if cut.found and cut.drop_db >= 20:
        noise_var = 10 ** (cut.floor_above_db / 10) / float(np.sum(np.hanning(nfft) ** 2))
        top_hz = cut.freq_hz
    else:
        noise_var = spectral.rounding_noise_var(stats.analysis_bits)
        top_hz = min(19000.0, sr * 0.45)

    step("Looking for quantization artifacts", 0.3)
    holes = spectral.holes_and_dropouts(mono, sr, top_hz, noise_var)
    step("Checking the stereo image", 0.4)
    stereo = spectral.stereo_analysis(audio.samples, sr)
    grids = []
    if grid_scan:
        grids = structure.detect_grids(
            audio.samples, sr, top_hz * 0.97, 2.0 ** -15,
            on_candidate=lambda label, i, n: step(f"Checking for {label.split(' (')[0]} frame structure", 0.45 + 0.5 * i / n))

    report = Report(audio, stats, cut, holes, stereo, grids)
    report.freqs, report.spectrum_db = freqs, spec_db
    report.noise_floor_db = float(spectral.db(spectral.bin_power(spectral.rounding_noise_var(16), nfft)))
    _spectrogram_for_plot(report, power, nfft / sr)

    step("Weighing the evidence", 0.95)
    _judge(report, hard, nyq)
    return report


def _spectrogram_for_plot(report: Report, power: np.ndarray, hop_s: float, max_cols: int = 1500) -> None:
    step = max(1, int(np.ceil(len(power) / max_cols)))
    trimmed = power[: len(power) // step * step]
    if len(trimmed) == 0:
        trimmed, step = power, 1
    pooled = trimmed.reshape(-1, step, power.shape[1]).mean(axis=1)
    report.spectrogram_db = spectral.db(pooled).T
    report.spectrogram_hop_s = hop_s * step


def _judge(r: Report, hard: bool, nyq: float) -> None:
    a, cut, f = r.audio, r.cutoff, r.findings
    lossy_family = _codec_family(a.codec) if a.is_lossy_container else None

    # ---- cutoff
    if cut.found and cut.drop_db >= 15:
        hint = bitrate.describe_cutoff(cut.freq_hz)
        desc = (f"Spectrum falls {cut.drop_db:.0f} dB within ~{cut.transition_hz:.0f} Hz at "
                f"{cut.freq_hz / 1000:.2f} kHz and stays down to Nyquist ({nyq / 1000:.2f} kHz).")
        if cut.freq_hz <= 20600:
            desc += f" Matches {hint}."
        if hard and cut.freq_hz <= 20600:
            f.append(Finding("Encoder lowpass (frequency cutoff)", "strong", 40, desc))
        elif cut.freq_hz <= 20600:
            f.append(Finding("Possible encoder lowpass", "moderate", 15, desc))
        elif hard and nyq <= 24000 and cut.freq_hz < nyq * 0.97:
            f.append(Finding("Steep lowpass just below Nyquist", "weak", 5,
                             desc + " Typical of resampling or anti-alias filters; occasionally high-bitrate AAC."))
        else:
            f.append(Finding("Band limit", "info", 0, desc))

        if hard and cut.freq_hz <= 20600 and cut.frames_exceeding is not None and cut.frames_exceeding < 0.01:
            f.append(Finding("Cutoff never exceeded", "moderate", 10,
                             f"{cut.frames_at_ceiling * 100:.0f}% of loud frames have strong content right up to "
                             f"the cutoff, yet {(1 - cut.frames_exceeding) * 100:.1f}% of them stop dead there - "
                             f"an artificial ceiling rather than the music's natural rolloff."))
        if cut.floor_above_db is not None and r.noise_floor_db is not None:
            rel = cut.floor_above_db - r.noise_floor_db
            f.append(Finding("Floor above the cutoff", "info", 0,
                             f"{rel:+.1f} dB relative to plain 16-bit rounding noise"
                             + (" - cleaner than any dithered 16-bit recording, so the band was removed digitally."
                                if rel < -6 else ".")))
    else:
        f.append(Finding("Frequency cutoff", "none", 0,
                         f"No cliff in the spectrum (largest drop {cut.drop_db:.1f} dB); content rolls off naturally "
                         f"toward Nyquist ({nyq / 1000:.2f} kHz)."))

    # ---- quantization artefacts in the spectrogram
    h = r.holes
    band = f"{h.band_hz[0] / 1000:.1f}-{h.band_hz[1] / 1000:.1f} kHz"
    pct = h.hole_fraction * 100
    if pct >= 1.0:
        f.append(Finding("Spectral holes (bands quantized to zero)", "strong", 20,
                         f"{pct:.2f}% of active time-frequency cells in {band} drop to the noise floor while the "
                         f"surrounding band is >22 dB louder. Unprocessed audio shows ~0%."))
    elif pct >= 0.3:
        f.append(Finding("Spectral holes (bands quantized to zero)", "moderate", 10,
                         f"{pct:.2f}% of active cells in {band} sit at the noise floor inside an otherwise loud band."))
    else:
        f.append(Finding("Spectral holes", "none", 0, f"{pct:.2f}% of active cells in {band} (no sign of zeroed bands)."))

    rate = h.dropout_rate * 100
    if rate >= 5:
        f.append(Finding("High band switching on/off", "strong", 15,
                         f"The top band toggles between active and silent on {rate:.1f}% of frame transitions while "
                         f"the midrange stays steady (encoder dropping bands it can't afford, e.g. LAME sfb21)."))
    elif rate >= 2:
        f.append(Finding("High band switching on/off", "moderate", 8,
                         f"The top band toggles on/off on {rate:.1f}% of frame transitions with steady midrange."))
    else:
        f.append(Finding("High band switching", "none", 0, f"{rate:.1f}% of frame transitions (natural decay)."))

    # ---- stereo
    s = r.stereo
    if s.applicable:
        if s.side_cutoff_hz and s.mid_cutoff_hz and s.side_cutoff_hz < s.mid_cutoff_hz - 1500:
            f.append(Finding("Side channel band-limited (joint stereo)", "strong", 15,
                             f"L-R content stops at {s.side_cutoff_hz / 1000:.1f} kHz while L+R continues to "
                             f"{s.mid_cutoff_hz / 1000:.1f} kHz - a joint/intensity-stereo encoder signature."))
        elif s.side_drop_db is not None and s.side_drop_db >= 6:
            f.append(Finding("Stereo width collapses at high frequencies", "weak", 5,
                             f"Side/mid ratio is {s.side_drop_db:.1f} dB lower just under the cutoff than in the "
                             f"midrange (stereo coupling in Vorbis/Opus/AAC, though some mixes do this naturally)."))
        else:
            f.append(Finding("Stereo image", "none", 0, "Side channel extends as far as the mid channel."))
    else:
        f.append(Finding("Stereo image", "info", 0, "Mono (or near-mono) - stereo checks skipped."))

    # ---- coding-frame grid
    if r.grids:
        g = r.grids[0]
        foreign = lossy_family is None or not g.label.startswith(lossy_family)
        if g.level != "none" and foreign:
            # z in the hundreds means the codec's own quantized coefficients were recovered: near-proof
            pts = 60 if g.zscore >= 50 else 45 if g.level == "strong" else 25
            f.append(Finding("Codec frame grid", g.level, pts,
                             f"Re-analysing with a {g.label} filterbank makes quantized coefficients reappear at "
                             f"offset {g.offset} (z = {g.zscore:.0f}, {g.agreeing_stats}/3 statistics agree). "
                             f"Audio that was never encoded has no preferred alignment."))
        elif g.level != "none":
            f.append(Finding("Codec frame grid", "info", 0,
                             f"The file's own {g.label} grid is visible (offset {g.offset}, z = {g.zscore:.0f})."))
        else:
            f.append(Finding("Codec frame grid", "none", 0,
                             f"No MP3/AAC/Vorbis block alignment found (best z = {g.zscore:.1f} for {g.label})."))

    # ---- lossy container whose content implies an earlier, lower-bitrate generation
    if a.is_lossy_container:
        expected = bitrate.expected_min_cutoff(a.codec, a.bitrate_kbps)
        if expected and hard and cut.freq_hz < expected:
            f.append(Finding("Cutoff too low for this file's bitrate", "strong", 50,
                             f"A {a.bitrate_kbps:.0f} kbps {_codec_family(a.codec) or a.codec} would normally keep "
                             f"content above ~{expected / 1000:.1f} kHz, but it stops at {cut.freq_hz / 1000:.2f} kHz - "
                             f"like a ~{bitrate.lame_bitrate_for_cutoff(cut.freq_hz)} kbps MP3. It was re-encoded "
                             f"from a lower-bitrate source."))

    # ---- upconversion (not lossy evidence by itself)
    st = r.samples
    if st.padded:
        r.upconversion.append(f"Only {st.effective_bits} of {st.container_bits} bits are used - "
                              f"{st.effective_bits}-bit audio padded into a {st.container_bits}-bit container.")
    elif st.is_float and st.effective_bits:
        r.upconversion.append(f"Float file whose samples are all exact {st.effective_bits}-bit values.")
    if st.missing_code_fraction is not None and st.missing_code_fraction >= 0.05:
        r.upconversion.append(f"{st.missing_code_fraction * 100:.0f}% of sample codes near zero are never used - "
                              f"gain was applied after an earlier, coarser quantization.")
    if cut.found and cut.drop_db >= 20 and nyq > 24500 and cut.freq_hz <= 24500:
        src = "44.1/48 kHz" if cut.freq_hz > 20600 else "lossy 44.1/48 kHz"
        r.upconversion.append(f"Content stops at {cut.freq_hz / 1000:.1f} kHz in a {a.sample_rate / 1000:g} kHz file - "
                              f"upsampled from a {src} source.")
    if a.is_lossy_container and st.peak_dbfs > 0:
        r.upconversion.append(f"Decoded peak reaches {st.peak_dbfs:+.1f} dBFS: coding noise pushes a loud master over "
                              f"full scale (more so at lower bitrates); it will clip if converted to integer PCM.")
    elif st.clip_runs:
        r.upconversion.append(f"{st.clip_runs} runs of 3+ full-scale samples (clipping; common when a lossy decode "
                              f"that overshoots 0 dBFS is saved to integer PCM, but also in loud masters).")

    f.sort(key=lambda x: (LEVEL_ORDER[x.level], -x.points))
    r.score = min(100, sum(x.points for x in f))

    grid_hit = next((g for g in r.grids if g.level != "none"), None)
    codec = grid_hit.label.split(" (")[0] if grid_hit else None
    rate_hint = bitrate.describe_cutoff(cut.freq_hz) if hard and cut.freq_hz <= 20600 else None
    if codec or rate_hint:
        r.source_guess = ", ".join(x for x in (f"{codec} encoder" if codec else None, rate_hint) if x)

    # ---- quality grade: how much the (earliest) lossy encode took away
    lossy = a.is_lossy_container or r.score >= 35
    if hard and cut.freq_hz <= 20600:
        r.quality, label = bitrate.quality_for_cutoff(cut.freq_hz)
        r.quality_label = f"equivalent to an MP3 at {label}"
    elif lossy:
        r.quality, r.quality_label = 95, "full-band lossy (very high bitrate, no encoder lowpass)"
    else:
        r.quality, r.quality_label = 100, "lossless / full bandwidth"

    if a.is_lossy_container:
        family = _codec_family(a.codec) or a.codec
        kbps = f"{a.bitrate_kbps:.0f} kbps " if a.bitrate_kbps else ""
        if any(x.title.startswith("Cutoff too low") for x in f) or any(
                x.title == "Codec frame grid" and x.points for x in f):
            r.verdict = f"TRANSCODED - this {kbps}{family} file was made from an earlier, lower-quality lossy encode"
        elif r.quality >= 95:
            r.verdict = f"Top-quality lossy file ({kbps}{family}); no sign of an earlier, lower-bitrate generation"
        else:
            r.verdict = (f"Reduced-quality lossy file ({kbps}{family}); its content matches its own bitrate, "
                         f"no sign of an earlier, lower-bitrate generation")
    elif r.score >= 60:
        r.verdict = "VERY LIKELY sourced from lossy-compressed audio"
    elif r.score >= 35:
        r.verdict = "PROBABLY sourced from lossy-compressed audio"
    elif r.score >= 15:
        r.verdict = "INCONCLUSIVE - some indicators of lossy coding"
    else:
        r.verdict = "No evidence of lossy compression found"


def status_of(r: Report) -> tuple[str, str]:
    """(kind, label) summarising the verdict: kind is good / warn / bad for colouring."""
    if r.verdict.startswith("TRANSCODED"):
        return "bad", "Transcoded"
    if r.audio.is_lossy_container:
        return ("good", "Looks good") if r.quality >= 95 else ("warn", "Reduced quality")
    if r.score >= 35:
        return "bad", "Lossy source"
    return ("warn", "Inconclusive") if r.score >= 15 else ("good", "Looks good")
