#!/usr/bin/env python
"""Resident HTTP daemon for the ESP32-S3 keyboard.

Opens COM7 exactly once, in the non-resetting DTR/RTS state, and never closes it.
Every HTTP request is served from that single handle, so the board is never
rebooted and the USB HID link the target enumerated stays up.

  python tools/kbd_server.py --port 7788

API (GET or POST unless noted):

  /                     tiny status page
  /health               {"ok":true,"port":"COM7"}
  /status               firmware STATUS  (started= is the mount counter)
  /vbus                 firmware VBUS register dump
  /key/<NAME>           tap a key            &hold=ms  &gap=ms
  /seq?keys=UP,DOWN,ENTER&gap=250
  /hold/<NAME>          press and keep held
  /release/<NAME>
  /ra                   release all
  /type?text=hello      type a string

Replies are {"ok":bool,"reply":"OK K UP","err":null}.
"""
import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

import serial

PORT = "COM7"
BAUD = 115200


class Kbd:
    """Single serial handle, serialised access, never reopened."""

    def __init__(self, port):
        self.lock = threading.Lock()
        self.ser = serial.Serial(port, BAUD, timeout=0.15, write_timeout=2)
        # DTR high / RTS low is the only combination that does not pulse the
        # auto-reset circuit. Anything else reboots the ESP32 and drops the link.
        self.ser.dtr = True
        self.ser.rts = False
        time.sleep(0.3)
        self.ser.reset_input_buffer()
        self.reconnects = 0

    def cmd(self, line, timeout=3.0, hold_ms=None, gap_ms=None):
        with self.lock:
            if not self.ser.is_open:
                raise RuntimeError("serial port is closed")
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
                            if ln.startswith(("OK", "ERR", "PONG")):
                                return ln
            return None

    def tap(self, name, hold_ms=200, gap_ms=120):
        """Hold, release, settle -- a real keypress rather than a 1 ms blip."""
        r1 = self.cmd(f"H {name}")
        time.sleep(max(hold_ms, 20) / 1000.0)
        r2 = self.cmd(f"R {name}")
        time.sleep(max(gap_ms, 10) / 1000.0)
        return r1, r2


KBD = None


class Handler(BaseHTTPRequestHandler):
    server_version = "esp32kbd/1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _arg(self, qs, name, default=None):
        v = qs.get(name)
        return unquote(v[0]) if v else default

    def do_GET(self):
        self.route()

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode(errors="replace") if length else ""
        self._post_body = body
        self.route()

    def route(self):
        try:
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            path = u.path.rstrip("/") or "/"
            parts = [p for p in path.split("/") if p]

            if path == "/":
                html = (b"<!doctype html><title>esp32kbd</title>"
                        b"<h3>esp32kbd daemon</h3><pre>"
                        b"GET /status  /vbus  /ra\n"
                        b"GET /key/UP  /key/ENTER?hold=300\n"
                        b"GET /seq?keys=UP,DOWN,ENTER\n"
                        b"GET /hold/DOWN  /release/DOWN\n"
                        b"GET /type?text=hello</pre>")
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
                return

            if path == "/health":
                with KBD.lock:
                    open_ = KBD.ser.is_open
                return self._send({"ok": open_, "port": KBD.ser.port})

            if path == "/status":
                return self._send({"ok": True, "reply": KBD.cmd("STATUS")})
            if path == "/vbus":
                return self._send({"ok": True, "reply": KBD.cmd("VBUS")})
            if path == "/ra":
                return self._send({"ok": True, "reply": KBD.cmd("RA")})

            if len(parts) == 2 and parts[0] in ("key", "hold", "release"):
                name = parts[1].upper()
                if parts[0] == "key":
                    hold = int(self._arg(qs, "hold", 200))
                    gap = int(self._arg(qs, "gap", 120))
                    r1, r2 = KBD.tap(name, hold, gap)
                    return self._send({"ok": r1 is not None, "reply": r1, "release": r2})
                if parts[0] == "hold":
                    return self._send({"ok": True, "reply": KBD.cmd(f"H {name}")})
                return self._send({"ok": True, "reply": KBD.cmd(f"R {name}")})

            if path == "/seq":
                keys = [k.strip().upper() for k in (self._arg(qs, "keys", "") or "").split(",") if k.strip()]
                gap = int(self._arg(qs, "gap", 250))
                if not keys:
                    return self._send({"ok": False, "err": "no keys"}, 400)
                out = []
                for k in keys:
                    r1, _ = KBD.tap(k, 200, gap)
                    out.append(r1)
                return self._send({"ok": True, "replies": out})

            if path == "/type":
                text = self._arg(qs, "text")
                if text is None and getattr(self, "_post_body", ""):
                    text = self._post_body
                if not text:
                    return self._send({"ok": False, "err": "no text"}, 400)
                return self._send({"ok": True, "reply": KBD.cmd(f"T {text}", timeout=5)})

            return self._send({"ok": False, "err": "unknown path"}, 404)
        except Exception as exc:  # noqa: BLE001 - report, never crash the daemon
            return self._send({"ok": False, "err": f"{type(exc).__name__}: {exc}"}, 500)


def main():
    global KBD, PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", default="COM7")
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--http-port", type=int, default=7788)
    a = ap.parse_args()
    PORT = a.port
    KBD = Kbd(PORT)
    srv = ThreadingHTTPServer((a.bind, a.http_port), Handler)
    print(f"esp32kbd daemon holding {PORT} on http://{a.bind}:{a.http_port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
