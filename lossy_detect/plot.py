"""Diagnostic figure: spectrogram, long-term spectrum, and the frame-grid scan.

Drawn in the app's dark theme (colours match ui/index.html) so it sits inside a
results card without a white box around it.
"""

from __future__ import annotations

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .analyze import Report

# Same values as the CSS custom properties in ui/index.html
SURFACE = "#171a21"   # --surface: figure background (the card the plot sits in)
PANEL = "#12151b"     # slightly darker plot areas
BORDER = "#2a2f3a"    # --border: spines and grid
TEXT = "#e8eaf0"      # --text
MUTED = "#9097a6"     # --muted
# Data colours, picked for contrast on PANEL
SPECTRUM = "#8aa4ff"  # light accent blue
CUTOFF = "#ff6b6b"    # red
MARKER = "#3fd5ff"    # cyan, for the cutoff line over the magma spectrogram
FLOOR = "#c3c9d6"     # light grey dotted reference
GRID_SCAN = "#c792ff"  # violet

DARK = {
    "figure.facecolor": SURFACE,
    "savefig.facecolor": SURFACE,
    "axes.facecolor": PANEL,
    "axes.edgecolor": BORDER,
    "axes.labelcolor": MUTED,
    "axes.titlecolor": TEXT,
    "axes.titleweight": "bold",
    "text.color": TEXT,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "grid.color": BORDER,
    "grid.alpha": 1.0,
    "grid.linewidth": 0.7,
    "legend.facecolor": "#1e222b",
    "legend.edgecolor": BORDER,
    "legend.labelcolor": TEXT,
    "legend.framealpha": 0.92,
    "font.family": ["Segoe UI", "DejaVu Sans"],
}


def save_plot(r: Report, out_path: str) -> str:
    with plt.rc_context(DARK):
        return _draw(r, out_path)


def _draw(r: Report, out_path: str) -> str:
    has_grid = bool(r.grids)
    fig, axes = plt.subplots(3 if has_grid else 2, 1, figsize=(12, 11 if has_grid else 8),
                             gridspec_kw={"height_ratios": [3, 2, 1.4] if has_grid else [3, 2]})
    name = os.path.basename(r.audio.path)
    score = "" if r.audio.is_lossy_container else f" (score {r.score}/100)"
    fig.suptitle(f"{name}\n{r.verdict}{score}\nQuality {r.quality}/100 - {r.quality_label}", fontsize=11)
    cutoff = r.cutoff.freq_hz if r.cutoff.found and r.cutoff.drop_db >= 15 else None

    # Spectrogram
    ax = axes[0]
    sg = r.spectrogram_db
    vmax = np.percentile(sg, 99.5)
    extent = [0, sg.shape[1] * r.spectrogram_hop_s, 0, r.freqs[-1] / 1000]
    im = ax.imshow(sg, origin="lower", aspect="auto", extent=extent, cmap="magma", vmin=vmax - 110, vmax=vmax)
    if cutoff:
        ax.axhline(cutoff / 1000, color=MARKER, lw=1.0, ls="--")
        ax.text(extent[1], cutoff / 1000, f" {cutoff / 1000:.2f} kHz ", color=MARKER, va="bottom", ha="right",
                fontsize=9, fontweight="bold")
    ax.set_ylabel("Frequency (kHz)")
    ax.set_xlabel("Time (s)")
    ax.set_title("Spectrogram")
    cb = fig.colorbar(im, ax=ax, pad=0.01)
    cb.set_label("dB", color=MUTED)
    cb.outline.set_edgecolor(BORDER)
    cb.ax.tick_params(colors=MUTED)

    # Long-term average spectrum
    ax = axes[1]
    ax.plot(r.freqs / 1000, r.spectrum_db, lw=0.8, color=SPECTRUM, label="average spectrum (loud frames)")
    if r.noise_floor_db is not None:
        ax.axhline(r.noise_floor_db, color=FLOOR, lw=0.9, ls=":", label="16-bit rounding noise")
    if cutoff:
        ax.axvline(cutoff / 1000, color=CUTOFF, lw=1.1, ls="--", label=f"cutoff {cutoff / 1000:.2f} kHz "
                   f"({r.cutoff.drop_db:.0f} dB drop)")
    top = np.percentile(r.spectrum_db, 99.9)
    ax.set_ylim(min(top - 140, (r.noise_floor_db or top) - 20), top + 5)
    ax.set_xlim(0, r.freqs[-1] / 1000)
    ax.set_xlabel("Frequency (kHz)")
    ax.set_ylabel("Power (dB)")
    ax.set_title("Long-term spectrum")
    ax.legend(loc="lower left", fontsize=8.5)
    ax.grid(True)

    # Frame-grid scan for the strongest candidate
    if has_grid:
        ax = axes[2]
        g = r.grids[0]
        ax.plot(np.arange(len(g.curve)), g.curve, lw=0.9, color=GRID_SCAN)
        if g.level != "none":
            ax.axvline(g.offset, color=CUTOFF, lw=1.0, ls="--")
        ax.set_title(f"Frame-grid scan: {g.label} - peak at offset {g.offset}, z = {g.zscore:.1f} ({g.level})")
        ax.set_xlabel("Block offset (samples)")
        ax.set_ylabel("Sparsity")
        ax.set_xlim(0, len(g.curve))
        ax.grid(True)

    fig.tight_layout()
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return out_path
