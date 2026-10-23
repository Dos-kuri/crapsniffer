"""Track metadata (artist, title, album, label, BPM, key...) and cover art, via mutagen.

Handles ID3 (MP3, WAV, AIFF), Vorbis comments (FLAC, OGG, Opus) and MP4 atoms
(M4A/AAC/ALAC). Missing or unreadable tags just give an empty result.
"""

from __future__ import annotations

import base64
import io

# field -> (ID3 frame, Vorbis comment keys, MP4 atom)
FIELDS = {
    "title": ("TIT2", ["title"], "\xa9nam"),
    "artist": ("TPE1", ["artist"], "\xa9ART"),
    "album": ("TALB", ["album"], "\xa9alb"),
    "album_artist": ("TPE2", ["albumartist", "album artist"], "aART"),
    "label": ("TPUB", ["label", "organization", "publisher"], None),
    "year": ("TDRC", ["date", "year"], "\xa9day"),
    "genre": ("TCON", ["genre"], "\xa9gen"),
    "bpm": ("TBPM", ["bpm"], "tmpo"),
    "key": ("TKEY", ["initialkey", "key"], None),
    "track": ("TRCK", ["tracknumber"], "trkn"),
}


def read_tags(path: str, cover_px: int = 320) -> dict:
    """Non-empty text fields, plus "cover" as a base64 JPEG data URL when there is artwork
    (skipped when cover_px is 0)."""
    try:
        import mutagen
        from .audio_io import long_path
        f = mutagen.File(long_path(path))
    except Exception:
        return {}
    if f is None or f.tags is None:
        return {}
    tags = f.tags
    kind = type(tags).__name__  # ID3, VCFLACDict / VComment variants, MP4Tags

    out = {}
    for name, (id3, vorbis, mp4) in FIELDS.items():
        value = None
        if kind == "ID3":
            frame = tags.get(id3)
            value = str(frame.text[0]) if frame and frame.text else None
        elif kind == "MP4Tags":
            raw = tags.get(mp4) if mp4 else None
            if raw:
                value = raw[0][0] if isinstance(raw[0], tuple) else raw[0]  # trkn is (n, total)
        else:
            for key in vorbis:
                if key in tags and tags[key]:
                    value = tags[key][0]
                    break
        if value not in (None, "", 0):
            out[name] = str(value).strip()

    if "year" in out:
        out["year"] = out["year"][:4]
    if "track" in out:
        out["track"] = out["track"].split("/")[0]
    if "bpm" in out:
        try:
            out["bpm"] = f"{float(out['bpm']):g}"
        except ValueError:
            pass

    cover = _cover_bytes(f, kind) if cover_px else None
    if cover:
        thumb = _thumbnail(cover, cover_px)
        if thumb:
            out["cover"] = "data:image/jpeg;base64," + base64.b64encode(thumb).decode("ascii")
    return out


def _cover_bytes(f, kind: str) -> bytes | None:
    try:
        if kind == "ID3":
            pics = f.tags.getall("APIC")
            front = [p for p in pics if p.type == 3]  # 3 = front cover
            return (front or pics)[0].data if pics else None
        if kind == "MP4Tags":
            covr = f.tags.get("covr")
            return bytes(covr[0]) if covr else None
        if getattr(f, "pictures", None):  # FLAC
            return f.pictures[0].data
        block = f.tags.get("metadata_block_picture")  # OGG/Opus
        if block:
            from mutagen.flac import Picture
            return Picture(base64.b64decode(block[0])).data
    except Exception:
        return None
    return None


def _thumbnail(data: bytes, px: int) -> bytes | None:
    """Shrink artwork (often 1400 px, 1+ MB) to a small JPEG for display."""
    try:
        from PIL import Image  # installed with matplotlib
        img = Image.open(io.BytesIO(data)).convert("RGB")
        img.thumbnail((px, px))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception:
        return None


def display_line(t: dict) -> str | None:
    """'Artist - Title' (or whichever half exists) for one-line listings."""
    parts = [t.get("artist"), t.get("title")]
    return " - ".join(p for p in parts if p) or None
