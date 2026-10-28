#!/usr/bin/env python
"""Capture stills from a USB webcam aimed at the target's monitor.

  python tools/cam.py list                 # enumerate indices, report frame stats
  python tools/cam.py grab [name]          # one frame -> shots/<name>.jpg
  python tools/cam.py burst N [name]       # N frames, 1s apart
  python tools/cam.py crop in out x y w h  # crop a region (e.g. the LCD panel)

Camera selection: index 1 = "HD 4MP WEBCAM" (external, pointed at monitor).
Index 0 = integrated laptop camera (black). Override with CAM_INDEX.
"""
import os
import sys
import time
import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "shots")
CAM_INDEX = int(os.environ.get("CAM_INDEX", "1"))
W, H = 2560, 1440  # 4MP sensor native
WARM = 12


def open_cam(index=None, res=(W, H), backend=cv2.CAP_DSHOW, warm=WARM):
    index = CAM_INDEX if index is None else index
    cap = cv2.VideoCapture(index, backend)
    if not cap.isOpened():
        cap.release()
        return None
    if res:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, res[0])
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, res[1])
    for _ in range(warm):  # flush auto-exposure settling frames
        cap.read()
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        return None
    return cap, frame


def settle_grab(cap):
    ok, frame = cap.read()
    return frame if ok else None


def cmd_list():
    for index in range(4):
        for bname, be in (("DSHOW", cv2.CAP_DSHOW), ("MSMF", cv2.CAP_MSMF)):
            try:
                r = open_cam(index, (W, H), be)
            except Exception as e:  # noqa: BLE001
                print(f"idx {index} {bname}: error {e}")
                continue
            if not r:
                print(f"idx {index} {bname}: unavailable")
                continue
            cap, frame = r
            cap.release()
            h, w = frame.shape[:2]
            tag = "external-4MP" if index == CAM_INDEX and w >= 1920 else ""
            print(f"idx {index} {bname}: {w}x{h} mean={frame.mean():6.2f} {tag}")


def save(frame, name):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.abspath(os.path.join(OUT, f"{name}.jpg"))
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    if not os.path.exists(path):
        raise RuntimeError(f"failed to write {path}")
    h, w = frame.shape[:2]
    print(f"{path} {w}x{h} mean={frame.mean():.2f}")


def cmd_grab(name="cap"):
    r = open_cam()
    if not r:
        sys.exit(f"camera {CAM_INDEX} not available")
    cap, frame = r
    nxt = settle_grab(cap)
    if nxt is not None:
        frame = nxt
    cap.release()
    save(frame, name)


def cmd_burst(n=3, name="burst"):
    r = open_cam()
    if not r:
        sys.exit(f"camera {CAM_INDEX} not available")
    cap, _ = r
    for i in range(n):
        frame = settle_grab(cap)
        if frame is not None:
            save(frame, f"{name}_{i:02d}")
        time.sleep(1.0)
    cap.release()


def cmd_crop(src, dst, x, y, w, h):
    img = cv2.imread(src)
    if img is None:
        sys.exit(f"cannot read {src}")
    patch = img[int(y):int(y) + int(h), int(x):int(x) + int(w)]
    cv2.imwrite(dst, patch, [cv2.IMWRITE_JPEG_QUALITY, 95])
    print(f"{dst} {patch.shape[1]}x{patch.shape[0]}")


if __name__ == "__main__":
    a = sys.argv[1:]
    cmd = a[0] if a else "grab"
    if cmd == "list":
        cmd_list()
    elif cmd == "grab":
        cmd_grab(a[1] if len(a) > 1 else "cap")
    elif cmd == "burst":
        cmd_burst(int(a[1]) if len(a) > 1 else 3, a[2] if len(a) > 2 else "burst")
    elif cmd == "crop" and len(a) == 7:
        cmd_crop(*a[1:])
    else:
        sys.exit(__doc__)
