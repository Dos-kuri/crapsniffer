"""Generate a synthetic lossless "master" plus known-transcoded versions of it.

Writes into test_files/ (or the folder given as the first argument):
  clean_16bit.wav           dithered 16-bit master, full bandwidth -> should come back clean
  clean_padded_24bit.flac   same master padded to 24 bits -> upconversion only, not lossy
  mp3_128.wav, mp3_320.wav  LAME CBR encode, decoded back to 16-bit WAV
  mp3_v2.flac               LAME VBR V2, decoded and stored as FLAC
  aac_128.wav               ffmpeg AAC 128k, decoded
  vorbis_q3.wav             libvorbis q3, decoded
  opus_96.wav               libopus 96k, decoded (48 kHz)
  fake_hires_96k.flac       MP3 192k decoded, resampled to 96 kHz / 24-bit
  mp3_128_as_320.mp3        MP3 128k re-encoded as a 320k MP3
  aac_256.wav               ffmpeg AAC 256k, decoded (little or no lowpass: tests the grid scan)
  clean_dull_16bit.wav      master through a gentle 6 kHz lowpass: natural rolloff, not lossy
  clean_resampled_48k.wav   master resampled to 48 kHz: steep filter near 22 kHz, not lossy
"""

import os
import subprocess
import sys

import imageio_ffmpeg
import numpy as np
import soundfile as sf
from scipy import signal

SR = 44100
SECONDS = 30
rng = np.random.default_rng(1234)


def envelope(n, attack, decay):
    t = np.arange(n) / SR
    return np.minimum(t / attack, 1.0) * np.exp(-t / decay)


def place(buf, start, sound, gain_l, gain_r):
    end = min(len(buf), start + len(sound))
    buf[start:end, 0] += sound[: end - start] * gain_l
    buf[start:end, 1] += sound[: end - start] * gain_r


def synth_master():
    n = SR * SECONDS
    out = np.zeros((n, 2))
    beat = int(SR * 0.5)  # 120 bpm

    hat_hp = signal.butter(4, 7000, "highpass", fs=SR, output="sos")
    snare_bp = signal.butter(2, [1500, 9000], "bandpass", fs=SR, output="sos")
    for b in range(SECONDS * 2):
        start = b * beat
        # kick: falling sine sweep
        m = int(0.4 * SR)
        t = np.arange(m) / SR
        kick = np.sin(2 * np.pi * (50 + 120 * np.exp(-t * 30)) * t) * envelope(m, 0.002, 0.12)
        place(out, start, kick * 0.8, 1, 1)
        # snare on 2 and 4
        if b % 2 == 1:
            m = int(0.3 * SR)
            snare = signal.sosfilt(snare_bp, rng.standard_normal(m)) * envelope(m, 0.001, 0.07)
            place(out, start, snare * 0.9, 0.9, 1.0)
        # hi-hats on every 8th, alternating pan, energy all the way to Nyquist
        for half in (0, beat // 2):
            m = int(0.08 * SR)
            hat = signal.sosfilt(hat_hp, rng.standard_normal(m)) * envelope(m, 0.0005, 0.02)
            pan = rng.uniform(0.3, 1.0)
            place(out, start + half, hat * 0.5, pan, 1.3 - pan)

    # chord pads: band-unlimited sawtooth-like additive tones with detuned stereo voices
    chords = [[220.0, 277.18, 329.63], [196.0, 246.94, 293.66], [174.61, 220.0, 261.63], [164.81, 207.65, 246.94]]
    chord_len = beat * 4
    for c in range(n // chord_len + 1):
        start = c * chord_len
        m = min(chord_len, n - start)
        if m <= 0:
            break
        t = np.arange(m) / SR
        env = envelope(m, 0.05, 1.5)
        for f0 in chords[c % len(chords)]:
            for ch, detune in ((0, 0.998), (1, 1.002)):
                f = f0 * detune
                k = np.arange(1, int((SR / 2) / f))
                tone = (np.sin(2 * np.pi * np.outer(t, f * k) + rng.uniform(0, 2 * np.pi, len(k))) / k).sum(axis=1)
                out[start:start + m, ch] += tone * env * 0.05

    # stereo "room": decorrelated decaying-noise reverb per channel
    ir_len = int(1.2 * SR)
    for ch in range(2):
        ir = rng.standard_normal(ir_len) * np.exp(-np.arange(ir_len) / SR / 0.35) * 0.01
        out[:, ch] += signal.fftconvolve(out[:, ch], ir)[:n]

    # quiet broadband noise bed (tape hiss / room tone)
    out += rng.standard_normal(out.shape) * 10 ** (-70 / 20)
    return out / np.abs(out).max() * 10 ** (-1 / 20)


def to_16bit_dithered(x):
    q = 2.0 ** -15
    tpdf = (rng.uniform(-0.5, 0.5, x.shape) + rng.uniform(-0.5, 0.5, x.shape)) * q
    return np.clip(np.round((x + tpdf) / q), -32768, 32767).astype(np.int16)


def ffmpeg(*args):
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def main():
    folder = sys.argv[1] if len(sys.argv) > 1 else "test_files"
    os.makedirs(folder, exist_ok=True)
    p = lambda name: os.path.join(folder, name)

    master16 = to_16bit_dithered(synth_master())
    sf.write(p("clean_16bit.wav"), master16, SR, subtype="PCM_16")
    sf.write(p("clean_padded_24bit.flac"), master16.astype(np.int32) << 16, SR, subtype="PCM_24")
    master = master16 / 32768.0
    dull = signal.sosfilt(signal.butter(2, 6000, fs=SR, output="sos"), master, axis=0)
    sf.write(p("clean_dull_16bit.wav"), to_16bit_dithered(dull), SR, subtype="PCM_16")
    sf.write(p("clean_resampled_48k.wav"), to_16bit_dithered(signal.resample_poly(master, 160, 147, axis=0)),
             48000, subtype="PCM_16")

    src = p("clean_16bit.wav")
    tmp = p("_tmp")
    os.makedirs(tmp, exist_ok=True)
    for name, ext, enc, out_name, out_args in [
        ("mp3_128", "mp3", ["-c:a", "libmp3lame", "-b:a", "128k"], "mp3_128.wav", ["-c:a", "pcm_s16le"]),
        ("mp3_320", "mp3", ["-c:a", "libmp3lame", "-b:a", "320k"], "mp3_320.wav", ["-c:a", "pcm_s16le"]),
        ("mp3_v2", "mp3", ["-c:a", "libmp3lame", "-q:a", "2"], "mp3_v2.flac", ["-c:a", "flac", "-sample_fmt", "s16"]),
        ("aac_128", "m4a", ["-c:a", "aac", "-b:a", "128k"], "aac_128.wav", ["-c:a", "pcm_s16le"]),
        ("vorbis_q3", "ogg", ["-c:a", "libvorbis", "-q:a", "3"], "vorbis_q3.wav", ["-c:a", "pcm_s16le"]),
        ("opus_96", "opus", ["-c:a", "libopus", "-b:a", "96k"], "opus_96.wav", ["-c:a", "pcm_s16le"]),
        ("aac_256", "m4a", ["-c:a", "aac", "-b:a", "256k"], "aac_256.wav", ["-c:a", "pcm_s16le"]),
        ("mp3_192", "mp3", ["-c:a", "libmp3lame", "-b:a", "192k"], "fake_hires_96k.flac",
         ["-af", "aresample=96000:filter_size=64", "-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", "24"]),
    ]:
        coded = os.path.join(tmp, f"{name}.{ext}")
        ffmpeg("-i", src, *enc, coded)
        ffmpeg("-i", coded, *out_args, p(out_name))

    ffmpeg("-i", os.path.join(tmp, "mp3_128.mp3"), "-c:a", "libmp3lame", "-b:a", "320k", p("mp3_128_as_320.mp3"))
    print(f"Wrote test files to {folder}/ (encoded intermediates in {tmp}/)")


if __name__ == "__main__":
    main()
