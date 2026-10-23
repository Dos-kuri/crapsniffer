"""Load audio files into sample arrays, keeping what the container says about them.

libsndfile (via soundfile) handles WAV/AIFF/FLAC/OGG/MP3 directly. Anything it
can't open (M4A/AAC/ALAC/Opus/WMA...) is decoded with the ffmpeg binary that
ships with the imageio-ffmpeg package.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import tempfile
from dataclasses import dataclass

import numpy as np
import soundfile as sf

# libsndfile subtypes that are themselves lossy codecs
LOSSY_SUBTYPES = {"MPEG_LAYER_I", "MPEG_LAYER_II", "MPEG_LAYER_III", "VORBIS", "OPUS"}

# ffmpeg codec names that are lossy
LOSSY_CODECS = {
    "aac", "mp3", "mp2", "mp1", "vorbis", "opus", "wmav1", "wmav2", "wmapro",
    "ac3", "eac3", "atrac3", "atrac3p", "atrac9", "cook", "ra_144", "ra_288",
    "amr_nb", "amr_wb", "speex", "gsm", "musepack7", "musepack8", "nellymoser",
}

PCM_BITS = {"PCM_S8": 8, "PCM_U8": 8, "PCM_16": 16, "PCM_24": 24, "PCM_32": 32}

PREFIX = "\\\\?\\"  # Windows extended-length path prefix

AUDIO_EXTENSIONS = {
    ".wav", ".flac", ".aif", ".aiff", ".mp3", ".m4a", ".aac", ".ogg", ".oga",
    ".opus", ".wma", ".alac", ".caf", ".w64", ".rf64", ".wv", ".ape", ".mp2",
}


@dataclass
class Audio:
    path: str
    samples: np.ndarray            # float64, shape (frames, channels), full scale = 1.0
    int_samples: np.ndarray | None  # int32, left-aligned, only for integer PCM containers
    sample_rate: int
    container: str                 # e.g. "FLAC", "WAV", "MP3", or the ffmpeg format name
    codec: str                     # e.g. "PCM_16", "MPEG_LAYER_III", "aac"
    container_bits: int | None     # integer PCM word length; None for float/lossy
    is_float: bool
    is_lossy_container: bool
    bitrate_kbps: float | None     # average bitrate of the audio stream (tags and cover art excluded when known)
    vbr: bool | None = None        # MP3 only: Xing header = VBR/ABR, Info header = CBR
    encoder: str | None = None     # MP3 only: encoder string from the LAME/Lavc tag

    @property
    def channels(self) -> int:
        return self.samples.shape[1]

    @property
    def duration(self) -> float:
        return self.samples.shape[0] / self.sample_rate


def long_path(path: str) -> str:
    """Path usable by Python file APIs even beyond Windows' 260-character MAX_PATH.

    Long paths get the extended-length prefix; shorter ones (and other OSes) are unchanged.
    ffmpeg handles long paths itself and must be given the plain path.
    """
    if os.name != "nt":
        return path
    full = os.path.abspath(path)
    if len(full) < 240 or full.startswith(PREFIX):
        return path
    return PREFIX + full


def load(path: str) -> Audio:
    try:
        return _load_soundfile(path)
    except (sf.LibsndfileError, RuntimeError, TypeError):
        return _load_ffmpeg(path)


def _load_soundfile(path: str) -> Audio:
    info = sf.info(long_path(path))
    bits = PCM_BITS.get(info.subtype)
    int_samples = None
    if bits is not None:
        int_samples = sf.read(long_path(path), dtype="int32", always_2d=True)[0]
        samples = int_samples.astype(np.float64) / 2.0**31
    else:
        samples = sf.read(long_path(path), dtype="float64", always_2d=True)[0]

    duration = samples.shape[0] / info.samplerate
    bitrate = os.path.getsize(long_path(path)) * 8 / duration / 1000 if duration > 0 else None
    vbr = encoder = None
    if info.subtype == "MPEG_LAYER_III":
        vbr, encoder, audio_bytes = mp3_header(path)
        if audio_bytes and duration > 0:
            bitrate = audio_bytes * 8 / duration / 1000
    return Audio(
        path=path,
        samples=samples,
        int_samples=int_samples,
        sample_rate=info.samplerate,
        container=info.format,
        codec=info.subtype,
        container_bits=bits,
        is_float=info.subtype in ("FLOAT", "DOUBLE"),
        is_lossy_container=info.subtype in LOSSY_SUBTYPES,
        bitrate_kbps=bitrate,
        vbr=vbr,
        encoder=encoder,
    )


def mp3_header(path: str) -> tuple[bool | None, str | None, int | None]:
    """Read the Xing/Info frame at the start of an MP3: (is_vbr, encoder, audio byte count)."""
    with open(long_path(path), "rb") as fh:
        head = fh.read(10)
        skip = 0
        if head[:3] == b"ID3":  # ID3v2 tag (may hold cover art) comes first
            skip = 10 + ((head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9])
        fh.seek(skip)
        b = fh.read(16384)
    pos = next((i for i in range(len(b) - 4) if b[i] == 0xFF and (b[i + 1] & 0xE6) == 0xE2), None)
    if pos is None:
        return None, None, None
    mpeg1 = (b[pos + 1] >> 3) & 3 == 3
    mono = (b[pos + 3] >> 6) == 3
    side = (17 if mono else 32) if mpeg1 else (9 if mono else 17)
    x = pos + 4 + side
    tag = b[x:x + 4]
    if tag not in (b"Xing", b"Info"):
        return None, None, None  # plain CBR without a header, or a VBRI (Fraunhofer) header
    flags = struct.unpack(">I", b[x + 4:x + 8])[0]
    o = x + 8 + (4 if flags & 1 else 0)
    audio_bytes = struct.unpack(">I", b[o:o + 4])[0] if flags & 2 else None
    o += (4 if flags & 2 else 0) + (100 if flags & 4 else 0) + (4 if flags & 8 else 0)
    encoder = b[o:o + 9].rstrip(bytes(1)).decode("ascii", "replace").strip() or None
    return tag == b"Xing", encoder, audio_bytes


# Keeps ffmpeg from flashing a console window when run from the windowed app
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as e:
        raise RuntimeError("This format needs ffmpeg; install the imageio-ffmpeg package") from e
    return imageio_ffmpeg.get_ffmpeg_exe()


def _load_ffmpeg(path: str) -> Audio:
    exe = _ffmpeg_exe()
    probe = subprocess.run([exe, "-hide_banner", "-i", path], capture_output=True, text=True, errors="replace",
                           creationflags=NO_WINDOW)
    stream = re.search(r"Stream #\S+.*?: Audio: (\w+)[^,]*, (\d+) Hz, ([^,]+), (\w+)(?:, (\d+) kb/s)?", probe.stderr)
    if not stream:
        raise RuntimeError(f"Could not find an audio stream in {path}")
    codec, rate, _layout, sample_fmt, stream_kbps = stream.groups()
    container = re.search(r"Input #0, ([^,]+)", probe.stderr)
    overall_kbps = re.search(r"bitrate: (\d+) kb/s", probe.stderr)

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "decoded.wav")
        # Keep the native sample rate and channel count; float output so nothing is rounded.
        subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error", "-i", path, "-map", "0:a:0",
             "-c:a", "pcm_f64le", out],
            check=True, capture_output=True, creationflags=NO_WINDOW,
        )
        samples = sf.read(out, dtype="float64", always_2d=True)[0]

    # Lossless integer codecs (ALAC, WavPack, APE...) report s16/s16p/s32/s32p
    bits = None
    if codec not in LOSSY_CODECS:
        m = re.match(r"s(16|32)", sample_fmt)
        bits = int(m.group(1)) if m else None
        bits_raw = re.search(r"\((\d+) bit\)", probe.stderr)
        if bits_raw:
            bits = int(bits_raw.group(1))
    int_samples = None
    if bits is not None:
        int_samples = np.round(samples * 2.0**31).clip(-2**31, 2**31 - 1).astype(np.int32)

    kbps = stream_kbps or (overall_kbps.group(1) if overall_kbps else None)
    return Audio(
        path=path,
        samples=samples,
        int_samples=int_samples,
        sample_rate=int(rate),
        container=container.group(1) if container else "unknown",
        codec=codec,
        container_bits=bits,
        is_float=sample_fmt.startswith("flt") or sample_fmt.startswith("dbl"),
        is_lossy_container=codec in LOSSY_CODECS,
        bitrate_kbps=float(kbps) if kbps else None,
    )
