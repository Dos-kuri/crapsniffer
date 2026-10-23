"""End-to-end checks against synthetic files with known history (tools/make_test_files.py).

Slow (~4 minutes): generates the fixtures once, then analyses each one.
"""

import os
import subprocess
import sys

import pytest

from lossy_detect.analyze import analyse_file

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = os.path.join(ROOT, "test_files")


@pytest.fixture(scope="session", autouse=True)
def fixtures():
    if not os.path.exists(os.path.join(FILES, "aac_256.wav")):
        subprocess.run([sys.executable, os.path.join(ROOT, "tools", "make_test_files.py"), FILES], check=True)


@pytest.mark.parametrize("name", [
    "clean_16bit.wav", "clean_padded_24bit.flac", "clean_dull_16bit.wav", "clean_resampled_48k.wav",
])
def test_lossless_sources_are_clean(name):
    r = analyse_file(os.path.join(FILES, name))
    assert r.score < 15, [f.title for f in r.findings if f.points]


@pytest.mark.parametrize("name, codec", [
    ("mp3_128.wav", "MP3"), ("mp3_320.wav", "MP3"), ("mp3_v2.flac", "MP3"),
    ("aac_128.wav", "AAC"), ("aac_256.wav", "AAC"), ("vorbis_q3.wav", "Vorbis"), ("opus_96.wav", "Opus"),
])
def test_lossy_sources_are_flagged(name, codec):
    r = analyse_file(os.path.join(FILES, name))
    assert r.score >= 60, r.findings
    assert r.grids[0].label.startswith(codec) and r.grids[0].level != "none"


def test_fake_hires():
    r = analyse_file(os.path.join(FILES, "fake_hires_96k.flac"), grid_scan=False)
    assert r.score >= 35
    assert any("upsampled from a lossy" in u for u in r.upconversion)


def test_padding_reported():
    r = analyse_file(os.path.join(FILES, "clean_padded_24bit.flac"), grid_scan=False)
    assert r.samples.effective_bits == 16 and r.samples.padded


def test_transcoded_mp3():
    r = analyse_file(os.path.join(FILES, "mp3_128_as_320.mp3"), grid_scan=False)
    assert r.verdict.startswith("TRANSCODED")


def test_honest_mp3_not_called_transcoded():
    r = analyse_file(os.path.join(FILES, "_tmp", "mp3_320.mp3"), grid_scan=False)
    assert not r.verdict.startswith("TRANSCODED")


@pytest.mark.parametrize("name, low, high", [
    ("mp3_320.wav", 95, 100), ("mp3_v2.flac", 75, 85), ("mp3_128.wav", 50, 60), ("clean_16bit.wav", 100, 100),
])
def test_quality_grade(name, low, high):
    r = analyse_file(os.path.join(FILES, name), grid_scan=False)
    assert low <= r.quality <= high, (r.quality, r.quality_label)


def test_mp3_header():
    from lossy_detect.audio_io import mp3_header
    vbr, encoder, audio_bytes = mp3_header(os.path.join(FILES, "_tmp", "mp3_v2.mp3"))
    assert vbr is True and audio_bytes
    vbr, _, _ = mp3_header(os.path.join(FILES, "mp3_128_as_320.mp3"))
    assert vbr is False
