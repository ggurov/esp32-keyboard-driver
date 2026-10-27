#!/usr/bin/env python
"""Drive a host BIOS through the ESP32-S3 HID keyboard (firmware/esp32kbd).

The board's UART (CH343, VID 1A86 PID 55D3) is the control channel; its native
USB port is the keyboard the target sees. `step` verifies a keypress by diffing
webcam frames, which is how you work when the target has no video path.

  python tools/kbd.py ping
  python tools/kbd.py press UP
  python tools/kbd.py press RIGHT -n 2
  python tools/kbd.py seq LEFT RIGHT ENTER ESC
  python tools/kbd.py type "hello"
  python tools/kbd.py hold DOWN / release DOWN / ra
  python tools/kbd.py step RIGHT          # press + webcam proof of screen change

Port override: KBD_PORT=COM7
"""
import argparse
import os
import socket
import sys
import time

import serial
from serial.tools import list_ports

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CH343 = (0x1A86, 0x55D3)
BAUD = 115200
DAEMON = ("127.0.0.1", 7788)


def find_port():
    env = os.environ.get("KBD_PORT")
    if env:
        return env
    cands = [p for p in list_ports.comports() if (p.vid, p.pid) == CH343]
    if not cands:
        cands = [p for p in list_ports.comports() if "CH343" in (p.description or "")]
    if not cands:
        raise SystemExit("ESP32 UART (CH343) not found; set KBD_PORT")
    return cands[0].device


class Kbd:
    def __init__(self, port=None, timeout=2.0, warm=0.0):
        self.port = port or find_port()
        self.ser = serial.Serial(self.port, BAUD, timeout=0.1, write_timeout=2)
        # DTR high / RTS low is the only combination that does NOT pulse the
        # auto-reset circuit. Anything else reboots the ESP32, which drops the
        # USB link the server has already enumerated.
        self.ser.dtr = True
        self.ser.rts = False
        time.sleep(0.2 + warm)
        self.ser.reset_input_buffer()
        self.timeout = timeout

    def cmd(self, line, expect_prefix=True, timeout=None):
        """Send one command and wait for its OK/ERR/PONG reply."""
        timeout = timeout or self.timeout
        self.ser.reset_input_buffer()
        self.ser.write((line + "\n").encode())
        self.ser.flush()
        deadline = time.time() + timeout
        buf = ""
        while time.time() < deadline:
            chunk = self.ser.read(256)
            if chunk:
                buf += chunk.decode(errors="replace")
                if "\n" in buf:
                    for ln in buf.splitlines():
                        ln = ln.strip()
                        if not ln or ln.startswith("READY"):
                            continue
                        if ln.startswith(("OK", "ERR", "PONG")):
                            return ln
        return None if expect_prefix else buf

    def close(self):
        self.ser.close()



class RemoteKbd:
    """Client for `kbd.py serve` -- keeps the serial port open so the board is
    never rebooted by a fresh open, which would drop the USB link."""

    def __init__(self):
        self.sock = socket.create_connection(DAEMON, timeout=5)
        self.buf = b""

    def cmd(self, line, expect_prefix=True, timeout=None):
        self.sock.sendall((line + "\n").encode())
        self.sock.settimeout(timeout or 5)
        while b"\n" not in self.buf:
            chunk = self.sock.recv(512)
            if not chunk:
                return None
            self.buf += chunk
        ln, _, self.buf = self.buf.partition(b"\n")
        return ln.decode(errors="replace").strip() or None

    def close(self):
        self.sock.close()


def cmd_serve():
    k = Kbd(warm=0.3)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(DAEMON)
    srv.listen(4)
    print(f"kbd daemon on {DAEMON[0]}:{DAEMON[1]} via {k.port}", flush=True)
    while True:
        conn, _ = srv.accept()
        with conn:
            f = conn.makefile("rwb", buffering=0)
            for raw in f:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                reply = k.cmd(line, timeout=5) or "TIMEOUT"
                f.write((reply + "\n").encode())

def daemon_alive():
    try:
        socket.create_connection(DAEMON, timeout=0.5).close()
        return True
    except OSError:
        return False


def get_kbd():
    if daemon_alive():
        return RemoteKbd()
    k = Kbd(warm=0.3)
    if k.cmd("PING", timeout=5) is None:
        raise SystemExit(f"no reply from {k.port} (board powered? sketch running?)")
    return k




def frame_diff(a, b):
    import numpy as np
    import cv2
    if a is None or b is None or a.shape != b.shape:
        return None
    g1 = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g2 = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g1 -= g1.mean()
    g2 -= g2.mean()
    d = np.abs(g1 - g2)
    return float(d.mean()), float((d > 40).mean() * 100.0)


def cmd_step(k, key, wait_ms, tag):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cam
    r = cam.open_cam()
    if not r:
        raise SystemExit("webcam unavailable")
    cap, frame = r
    before = cam.settle_grab(cap)
    if before is None:
        before = frame
    reply = k.cmd(f"K {key}")
    time.sleep(wait_ms / 1000)
    after = cam.settle_grab(cap)
    cap.release()
    if after is None:
        raise SystemExit("webcam dropped the after-frame")
    cam.save(before, f"{tag}_before")
    cam.save(after, f"{tag}_after")
    res = frame_diff(before, after)
    changed = res and res[1] > 0.2
    extra = f" mean={res[0]:.3f} pixels={res[1]:.3f}%" if res else ""
    print(f"{key:6s} {reply or 'no-reply'}{extra}  -> {'CHANGED' if changed else 'no visible change'}")
    return bool(changed)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("serve", help="hold the serial port open and serve commands over TCP")

    sub.add_parser("ping")
    sub.add_parser("id")
    sub.add_parser("status", help="USB mount counters (started>0 = host configured us)")
    sub.add_parser("vbus", help="dump USB OTG registers (VBUS / address / suspend state)")
    sub.add_parser("ra", help="release all keys")

    pp = sub.add_parser("press", help="tap a key")
    pp.add_argument("key")
    pp.add_argument("-n", type=int, default=1)
    pp.add_argument("-d", type=int, default=180, help="delay between taps (ms)")

    ps = sub.add_parser("seq", help="tap several keys")
    ps.add_argument("keys", nargs="+")
    ps.add_argument("-d", type=int, default=250)

    pt = sub.add_parser("type", help="type text")
    pt.add_argument("text")

    ph = sub.add_parser("hold")
    ph.add_argument("key")
    pr = sub.add_parser("release")
    pr.add_argument("key")

    pst = sub.add_parser("step", help="press a key and verify the screen changed (webcam)")
    pst.add_argument("key")
    pst.add_argument("-w", type=int, default=900, help="wait before the after-shot (ms)")
    pst.add_argument("-t", dest="tag", default=None)

    a = p.parse_args()
    k = get_kbd()
    try:
        if a.cmd == "serve":
            cmd_serve()
            return
        elif a.cmd == "ping":
            print(k.cmd("PING") or "no reply")
        elif a.cmd == "id":
            print(k.cmd("ID") or "no reply")
        elif a.cmd == "status":
            print(k.cmd("STATUS") or "no reply")
        elif a.cmd == "vbus":
            print(k.cmd("VBUS") or "no reply")
        elif a.cmd == "ra":
            print(k.cmd("RA") or "no reply")
        elif a.cmd == "press":
            for i in range(a.n):
                print(k.cmd(f"K {a.key}") or "no reply")
                if i < a.n - 1:
                    time.sleep(a.d / 1000)
        elif a.cmd == "seq":
            for i, key in enumerate(a.keys):
                print(k.cmd(f"K {key}") or "no reply")
                if i < len(a.keys) - 1:
                    time.sleep(a.d / 1000)
        elif a.cmd == "type":
            print(k.cmd(f"T {a.text}") or "no reply")
        elif a.cmd == "hold":
            print(k.cmd(f"H {a.key}") or "no reply")
        elif a.cmd == "release":
            print(k.cmd(f"R {a.key}") or "no reply")
        elif a.cmd == "step":
            cmd_step(k, a.key, a.w, a.tag or f"kbd_{a.key.lower()}")
    finally:
        k.close()


if __name__ == "__main__":
    main()
