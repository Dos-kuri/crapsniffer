"""Fail if any binary in the .app needs a newer macOS than the app promises to support.

The self-test runs on GitHub's macOS 15 machines, so it can't catch a library that only
loads on macOS 15 (1.0.1 shipped a Python built for 15.0 that failed on older Macs).

    python packaging/check_min_macos.py dist/CrapSniffer.app 14.0
"""

import os
import subprocess
import sys

MACHO_MAGIC = {b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
               b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}


def version(text):
    return tuple(int(p) for p in text.split("."))


def min_os(path):
    """Highest minimum macOS among the file's architectures, from its load commands."""
    out = subprocess.run(["otool", "-l", path], capture_output=True, text=True).stdout
    found = []
    lines = out.splitlines()
    for i, line in enumerate(lines):
        cmd = line.strip()
        if cmd == "cmd LC_BUILD_VERSION":
            key = "minos"
        elif cmd == "cmd LC_VERSION_MIN_MACOSX":
            key = "version"
        else:
            continue
        for follow in lines[i + 1:i + 6]:
            parts = follow.split()
            if len(parts) == 2 and parts[0] == key:
                found.append(version(parts[1]))
                break
    return max(found) if found else None


def main():
    app, limit = sys.argv[1], version(sys.argv[2])
    too_new, checked = [], 0
    for root, _, files in os.walk(app):
        for name in files:
            path = os.path.join(root, name)
            if os.path.islink(path):
                continue
            with open(path, "rb") as f:
                if f.read(4) not in MACHO_MAGIC:
                    continue
            checked += 1
            need = min_os(path)
            if need and need > limit:
                too_new.append((need, os.path.relpath(path, app)))
    print(f"   checked {checked} binaries against macOS {sys.argv[2]}")
    for need, rel in sorted(too_new, reverse=True):
        print(f"   FAIL needs macOS {'.'.join(map(str, need))}: {rel}")
    sys.exit(1 if too_new else 0)


if __name__ == "__main__":
    main()
