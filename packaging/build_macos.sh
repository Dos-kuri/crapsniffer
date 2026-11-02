#!/usr/bin/env bash
# Build CrapSniffer.app and a drag-to-Applications .dmg installer.
#
# Run ON a Mac (PyInstaller can't build macOS apps from Windows), from anywhere:
#     bash packaging/build_macos.sh
# Output: dist/CrapSniffer-<version>-macOS-<arm64|x86_64>.dmg
#
# The .dmg only runs on the CPU type it was built on: build on an Apple Silicon Mac for
# M1/M2/M3/M4 Macs, on an Intel Mac for Intel Macs (the GitHub workflow builds both).
#
# Optional environment variables:
#   CRAPSNIFFER_VERSION     version shown in Finder / About (default 1.0.0)
#   CRAPSNIFFER_MIN_MACOS   oldest macOS allowed to open it (default 14.0: numpy/scipy's
#                           Accelerate wheels need it); the build fails if any bundled
#                           binary needs a newer macOS than this
#   SKIP_SELF_TEST=1        skip analysing test audio with the packaged app
#   CODESIGN_IDENTITY       "Developer ID Application: Name (TEAMID)" - sign for real instead of ad-hoc
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD
#                           with CODESIGN_IDENTITY: notarize the .dmg so Macs open it without warnings
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(uname -s)" != "Darwin" ]; then
    echo "This script must run on macOS." >&2
    exit 1
fi

ARCH="$(uname -m)"
CRAPSNIFFER_VERSION="${CRAPSNIFFER_VERSION:-1.0.0}"
CRAPSNIFFER_MIN_MACOS="${CRAPSNIFFER_MIN_MACOS:-14.0}"
export CRAPSNIFFER_VERSION CRAPSNIFFER_MIN_MACOS
# Only uv's portable Python builds: a Homebrew or system Python on the build Mac is
# compiled for that Mac's macOS and links its system libraries (1.0.1 failed on older Macs).
export UV_PYTHON_PREFERENCE=only-managed

if ! command -v uv >/dev/null 2>&1; then
    echo "==> Installing uv (Python package manager)"
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

echo "==> Installing Python and dependencies"
uv sync --python 3.14 --frozen

echo "==> Building CrapSniffer.app ($ARCH, macOS $CRAPSNIFFER_MIN_MACOS+, version $CRAPSNIFFER_VERSION)"
rm -rf build dist
uv run --no-sync pyinstaller packaging/crapsniffer-macos.spec --noconfirm
APP="dist/CrapSniffer.app"

echo "==> Checking every bundled binary runs on macOS $CRAPSNIFFER_MIN_MACOS"
uv run --no-sync python packaging/check_min_macos.py "$APP" "$CRAPSNIFFER_MIN_MACOS"

if [ "${SKIP_SELF_TEST:-0}" != "1" ]; then
    echo "==> Self-test: analysing generated audio with the packaged app"
    uv run --no-sync python tools/make_test_files.py build/selftest >/dev/null
    "$APP/Contents/MacOS/CrapSniffer" --self-test build/selftest.json \
        build/selftest/clean_16bit.wav build/selftest/mp3_128.wav build/selftest/_tmp/aac_128.m4a
    uv run --no-sync python - <<'PY'
import json, sys
r = json.load(open("build/selftest.json"))
ok = r["frozen"] and r["ui_found"]
expect = {"clean_16bit.wav": (100, None), "mp3_128.wav": (55, "MP3"), "aac_128.m4a": (None, "AAC")}
for f in r["files"]:
    name = f["file"].rsplit("/", 1)[-1]
    want_q, want_grid = expect[name]
    good = "error" not in f and (want_q is None or f["quality"] == want_q) \
        and (want_grid is None or (f["grid"] or "").startswith(want_grid))
    ok &= good
    print(f"   {'ok  ' if good else 'FAIL'} {name}: {f.get('error') or (f['quality'], f['status'], f['grid'])}")
sys.exit(0 if ok else 1)
PY
fi

if [ -n "${CODESIGN_IDENTITY:-}" ]; then
    echo "==> Signing with $CODESIGN_IDENTITY"
    codesign --force --deep --options runtime --timestamp \
        --entitlements packaging/entitlements.plist --sign "$CODESIGN_IDENTITY" "$APP"
else
    echo "==> Ad-hoc signing (required for the app to launch on Apple Silicon)"
    codesign --force --deep --sign - "$APP"
fi
codesign --verify --deep --strict "$APP"

echo "==> Creating .dmg"
DMG="dist/CrapSniffer-${CRAPSNIFFER_VERSION}-macOS-${ARCH}.dmg"
STAGE="build/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
ditto "$APP" "$STAGE/CrapSniffer.app"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "CrapSniffer" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null

if [ -n "${CODESIGN_IDENTITY:-}" ] && [ -n "${APPLE_ID:-}" ]; then
    echo "==> Notarizing (takes a few minutes)"
    codesign --sign "$CODESIGN_IDENTITY" --timestamp "$DMG"
    xcrun notarytool submit "$DMG" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_PASSWORD" --wait
    xcrun stapler staple "$DMG"
fi

echo
echo "Done: $DMG ($(du -h "$DMG" | cut -f1))"
