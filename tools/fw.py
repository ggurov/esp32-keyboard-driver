#!/usr/bin/env python
"""Build and flash the esp32kbd firmware onto the ESP32-S3-USB-OTG board.

  python tools/fw.py boards            list boards matching s3/otg
  python tools/fw.py build
  python tools/fw.py upload [PORT]     default: CH343 COM port
  python tools/fw.py flash             build + upload in one go

The board's native USB port enumerates as the HID keyboard; flashing goes over
the CH343 UART port.
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
def _cli():
    env = os.environ.get("ARDUINO_CLI")
    if env:
        return env
    found = shutil.which("arduino-cli")
    if found:
        return found
    for name in ("arduino-cli.exe", "arduino-cli"):
        candidate = os.path.join(ROOT, "tools", "bin", name)
        if os.path.exists(candidate):
            return candidate
    raise SystemExit("arduino-cli not found: put it on PATH or set ARDUINO_CLI")


CLI = _cli()
SKETCH = os.path.join(ROOT, "fw", "esp32kbd")
FQBN_BASE = "esp32:esp32:esp32s3usbotg"
# This board's defaults are already correct: USBMode=USB-OTG (TinyUSB HID),
# UploadMode=UART0 (the CH343 console port).
FQBN = FQBN_BASE

CH343 = (0x1A86, 0x55D3)  # UART bridge on the ESP32-S3-USB-OTG board


def acli(*args, timeout=1800, quiet=False):
    cmd = [CLI, *args]
    if not quiet:
        print(">", " ".join(args), flush=True)
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if not quiet:
        if p.stdout.strip():
            print(p.stdout.strip()[-4000:])
        if p.stderr.strip():
            print("ERR:", p.stderr.strip()[-4000:])
    return p


def find_port():
    from serial.tools import list_ports
    for p in list_ports.comports():
        if (p.vid, p.pid) == CH343:
            return p.device
    for p in list_ports.comports():
        if "CH343" in (p.description or ""):
            return p.device
    raise SystemExit("CH343 UART port not found; pass PORT explicitly")


def main():
    a = sys.argv[1:]
    cmd = a[0] if a else "flash"

    if cmd == "boards":
        p = acli("board", "listall", quiet=True)
        for line in p.stdout.splitlines():
            low = line.lower()
            if "s3" in low or "otg" in low:
                print(line)
        return

    if cmd in ("build", "flash"):
        p = acli("compile", "--fqbn", FQBN, SKETCH)
        if p.returncode != 0:
            sys.exit("build failed")
        if cmd == "build":
            return

    if cmd in ("upload", "flash"):
        port = a[1] if len(a) > 1 else find_port()
        print("uploading to", port, "with", FQBN)
        p = acli("upload", "-p", port, "--fqbn", FQBN, SKETCH, timeout=600)
        if p.returncode != 0:
            sys.exit("upload failed")
        print("upload done")
        return

    sys.exit(__doc__)


if __name__ == "__main__":
    main()
