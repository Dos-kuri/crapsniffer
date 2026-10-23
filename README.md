# CrapSniffer

> The ultimate lie detector for your crates. Because fake "320kbps" sound like absolute shit on a real sound system.

Checks whether an audio file has been through lossy compression (MP3, AAC, Vorbis, Opus) at some point, even when it is now stored as WAV/FLAC/AIFF. It also checks whether a lossy file was re-encoded from an earlier, lower-bitrate copy.

## Desktop app

Double-click **`CrapSniffer.lnk`** in this folder (a copy is on the Desktop), or run:

```bash
uv run crapsniffer-app
```

Drag audio files, or whole folders, onto the window. You can also click *browse for files* (several can be selected at once) or *choose a folder*. Review the list, remove any file you don't want, then click **Analyze**. Files are analyzed **one at a time, in order**. A typical 5–8 minute track takes about 15 seconds.

- **One file** opens its full report straight away.
- **Several files** show a results table that fills in as each file finishes: the quality score with a colour bar, the result (*Looks good*, *Reduced quality*, *Lossy source*, *Transcoded*), the MP3 setting it is equivalent to, and the cutoff. Click a column header to sort. Click a row to open that file's full report, and use *All results* to come back. **Stop** finishes the current file and skips the rest. **Export CSV** saves the table.

A full report shows:

- the track's **artist, title, album, label, year, genre, BPM, key and cover art**, when the file is tagged;
- the **quality score** (0–100, where 320 kbps MP3 = 100) on a gauge, with the MP3 bitrate it is equivalent to;
- the verdict and key facts (format, bitrate, cutoff, peak);
- every finding, with its strength;
- the spectrogram and diagnostic plots, under a collapsible section.

The UI is always dark. The window is an HTML page (`lossy_detect/ui/index.html`) hosted by [pywebview](https://pywebview.flowrl.com/) in a native window, using the Edge WebView2 runtime built into Windows 10/11. `lossy_detect/app.py` connects it to the analysis engine.

## Sharing CrapSniffer on Windows (single .exe)

`dist/CrapSniffer.exe` is the whole program in one file (about 106 MB): Python, every library, ffmpeg and the UI. Copy it to any **64-bit Windows 10 or 11** PC and double-click it. Nothing else needs to be installed.

- **Startup:** a splash screen appears at once. The main window follows after about 10 seconds, because the one-file bundle unpacks itself to a temporary folder on each launch. The first launch of a freshly downloaded copy can take 20–30 seconds while Windows Defender scans it.
- **"Windows protected your PC":** the exe isn't code-signed, so SmartScreen warns about it when it was downloaded from the internet or a chat app. Click **More info → Run anyway**. Sending it inside a .zip, or over a USB stick or network share, usually avoids the prompt.
- **WebView2:** the window uses Microsoft Edge WebView2, which is built into Windows 11 and nearly every Windows 10 PC. If it's missing, CrapSniffer shows a message with the download link.

To rebuild after changing the code (close any running CrapSniffer first):

```bash
uv run pyinstaller packaging/crapsniffer.spec --noconfirm
```

The build config is `packaging/crapsniffer.spec`, with the icon and splash image beside it. To check that a build carries everything it needs, run `CrapSniffer.exe --self-test result.json some.mp3 other.m4a` from a terminal. It analyses the files without opening a window and writes the results, or any errors, to `result.json`.

## Sharing CrapSniffer on macOS (.dmg installer)

The Mac version is a standard installer: `CrapSniffer-<version>-macOS-arm64.dmg` for Apple Silicon Macs (M1–M4), or `…-x86_64.dmg` for Intel Macs. Open the .dmg, drag **CrapSniffer** onto **Applications**, and launch it from there. It needs macOS 14 Sonoma or newer on Apple Silicon, and macOS 12 or newer on Intel.

**A Mac app has to be built on a Mac.** PyInstaller can't produce one from Windows. There are two ways to get the .dmg:

1. **On any Mac:** copy this project folder over (without `.venv`, `build` and `dist`), open Terminal in it and run:

   ```bash
   bash packaging/build_macos.sh
   ```

   It installs everything it needs, builds the app, checks it on test audio, and writes the .dmg to `dist/`. It takes about 5–10 minutes. Build on an Apple Silicon Mac for Apple Silicon users, and on an Intel Mac for Intel users.
2. **Without a Mac, on GitHub (free):** the project lives at `github.com/Dos-kuri/crapsniffer`. GitHub builds both Mac .dmgs (Apple Silicon and Intel) and the Windows .exe on its own machines, and tests each one. The workflow is `.github/workflows/build.yml`.
   - **To publish a version,** push a tag such as `v1.2.0`:

     ```bash
     git tag v1.2.0
     ```

     ```bash
     git push origin v1.2.0
     ```

     When the builds pass, a **GitHub Release** named "CrapSniffer 1.2.0" appears on the repository's **Releases** page with all three installers attached and download instructions. Releases are kept permanently.
   - **For a test build without a release,** go to **Actions → Build installers → Run workflow**. The installers appear under that run's **Artifacts** and are deleted after 90 days.

**First launch on another Mac:** the app isn't signed with a paid Apple Developer ID, so macOS blocks it the first time with *"CrapSniffer" cannot be opened* or *"Apple could not verify…"*. To allow it once:

- **macOS 15 and newer:** try to open the app, click **Done**, then go to **System Settings → Privacy & Security**, scroll down, click **Open Anyway** next to CrapSniffer, and confirm.
- **macOS 14:** right-click (or Control-click) the app in Applications, choose **Open**, then click **Open**.
- **Or, in Terminal:** `xattr -dr com.apple.quarantine /Applications/CrapSniffer.app`

To remove these warnings entirely, join the Apple Developer Program ($99/year). Then set `CODESIGN_IDENTITY`, `APPLE_ID`, `APPLE_TEAM_ID` and `APPLE_APP_PASSWORD` before running the build script, and it will sign and notarize the .dmg.

Mac build files: `packaging/build_macos.sh`, `packaging/crapsniffer-macos.spec`, `packaging/entitlements.plist` and `packaging/crapsniffer.icns`. `python tools/make_icons.py` redraws the icons for both platforms.

## Command line

With several files or a folder, files are analyzed one at a time with `[n/total]` progress. A summary table (worst quality first) is printed at the end.

Requires [uv](https://docs.astral.sh/uv/). It installs Python and all dependencies, including an ffmpeg binary for M4A/AAC/ALAC/Opus/WMA input.

```bash
uv run crapsniffer "path/to/song.flac"
```

Other options:

```bash
uv run crapsniffer "D:/Music/Album" --plot --out-dir reports   # whole folder + diagnostic PNGs
uv run crapsniffer a.mp3 b.flac --csv results.csv               # several files + summary CSV
uv run crapsniffer song.wav --json                               # machine-readable output
uv run crapsniffer song.wav --fast                               # skip the frame-grid scan (~3x faster)
```

Example output for a 128 kbps MP3 decoded to WAV:

```
Verdict:  VERY LIKELY sourced from lossy-compressed audio  (score 100/100)
Likely source: MP3 encoder, ~128-160 kbps MP3 (LAME 3.98+, or VBR V4-V5), or ~128 kbps AAC

Evidence:
  [STRONG]   Codec frame grid (+45)
  [STRONG]   Encoder lowpass (frequency cutoff) (+40)
  [STRONG]   Spectral holes (bands quantized to zero) (+20)
  [MODERATE] Cutoff never exceeded (+10)
```

## What it looks for

| Test | Module | What lossy coding leaves behind |
|---|---|---|
| **Frequency cutoff** | `spectral.find_cutoff` | Encoders lowpass according to bitrate (LAME 128k ≈ 17 kHz, 320k ≈ 20.5 kHz). The test looks for a cliff of 25+ dB within a few hundred Hz that stays down to Nyquist. Natural rolloff drops only a few dB. The cutoff frequency maps to a likely bitrate (`bitrate.py`). |
| **Cutoff never exceeded** | `spectral.cutoff_consistency` | Takes frames with strong content right up to the cutoff and checks whether any of them carry content past it. Real instruments do, an encoder lowpass never lets them. |
| **Spectral holes** (quantization) | `spectral.holes_and_dropouts` | Scale-factor bands that the encoder quantized to zero show up as cells at the noise floor inside an otherwise loud band. Unprocessed audio shows ~0% of these; MP3 128k shows ~1%, Vorbis q3 ~5%. |
| **High-band switching** | `spectral.holes_and_dropouts` | The top band switching on and off from frame to frame (LAME sfb21, AAC band dropping) while the midrange stays steady. |
| **Joint stereo** | `spectral.stereo_analysis` | The side (L−R) channel is band-limited harder than the mid channel, or stereo width collapses near the cutoff. |
| **Codec frame grid** (structural) | `structure.py` | The most specific test. The audio is re-analysed with each codec's MDCT (AAC 1024/KBD+sine, Vorbis 2048, MP3 576, Opus CELT 960) at every possible offset. At the encoder's exact alignment, coefficients it zeroed come back as near-zeros, so one offset spikes. Audio that was never encoded has no preferred offset. This works even when there is no lowpass (e.g. AAC 256k) and identifies the codec family. |
| **Bit depth / upconversion** | `quantization.py` | Only 16 of 24 bits used (padding), unused sample codes (gain applied after quantization), clipping runs. A cutoff ≤ 24 kHz in a 96 kHz file is reported as upsampled ("fake hi-res"). |
| **Re-encoded lossy file** | `analyze._judge` | For MP3/AAC/Ogg/Opus input: a cutoff too low for the file's own bitrate (e.g. a "320 kbps" MP3 that stops at 16.8 kHz). The frame grid of a *different* codec than the container also counts. |

### Quality grade

Every file also gets a **quality score (320 kbps MP3 = 100)**. It is based on the cutoff, which is the most reliable fingerprint of the bitrate the audio was encoded at. The table below was calibrated by encoding real music with LAME 3.100 at every common setting and measuring the result with this tool:

| Measured cutoff | MP3 setting | Quality |
|---|---|---|
| 20.23 kHz | 320 kbps CBR / V0 | 100 |
| 19.56 kHz | 224–256 kbps / V1 | 90 |
| 18.86 kHz | 192 kbps / V2 | 80 |
| 18.17 kHz | V3 | 72 |
| 17.48 kHz | 160 kbps / V4 | 65 |
| 16.80 kHz | 128 kbps / V5–V6 | 55 |
| 15.44 kHz | 96–112 kbps / V7 | 40 |

Cutoffs between table rows are interpolated. Spectral holes are not used for grading because they vary too much with the music: V3 left 9% holes and 112k only 0.2% on the same track. A lossless file with no evidence scores 100. Lossy audio with no lowpass, such as AAC 256k, scores 95.

For MP3 input, the bitrate is read from the Xing/Info header. That excludes embedded cover art and tags, and it also tells VBR (Xing) apart from CBR (Info).

### Verdict

Each test adds points. The total sets the verdict: ≥60 *very likely*, 35–59 *probably*, 15–34 *inconclusive*, <15 *no evidence*.

`--plot` saves a PNG with three panels: the spectrogram with the cutoff marked, the long-term spectrum against the 16-bit noise floor, and the frame-grid scan curve.

## Limitations

- **Absence of evidence is not proof.** High-bitrate AAC/Opus with no lowpass, an encoder run with `--lowpass` disabled, or heavy post-processing (EQ, resampling, time-stretching, mastering, adding noise) can hide most signs. Resampling or pitch-shifting destroys the frame grid.
- **The cutoff can mislead on its own.** Old analogue recordings, telephone/AM sources, and deliberately lowpassed mixes have real cliffs too. Trust the verdict more when the cutoff and the grid or holes agree.
- **Bitrate guesses are approximate.** Lowpass tables differ between encoders and versions.
- **The grid test needs a few seconds of busy audio.** It scans the loudest 3 seconds, so near-silent or very sparse material can come back as "none".

## Tests

`tools/make_test_files.py` generates a synthetic master plus transcodes with known history in `test_files/`: MP3 128/320/V2, AAC 128/256, Vorbis, Opus, fake 96k hi-res, an MP3 re-encoded 128→320, and lossless controls (padded, dull, resampled). The test suite checks the verdict for each:

```bash
uv run pytest tests        # ~2-4 minutes
```

## Naming

The program, commands (`crapsniffer`, `crapsniffer-app`) and window are called CrapSniffer. The Python package inside is still `lossy_detect`; renaming it would change every import for no visible benefit.
