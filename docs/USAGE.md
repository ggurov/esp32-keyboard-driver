# Using the ESP32 keyboard driver

End-to-end: what to install, how to build and flash the firmware, how to drive a
target, and how to get out of the failure modes this thing is known for.

- [1. What you need](#1-what-you-need)
- [2. Wire it up](#2-wire-it-up)
- [3. Build the firmware](#3-build-the-firmware)
- [4. Flash the firmware](#4-flash-the-firmware)
- [5. Drive the target](#5-drive-the-target)
- [6. Verify with a camera](#6-verify-with-a-camera)
- [7. BIOS navigation cookbook](#7-bios-navigation-cookbook)
- [8. Troubleshooting](#8-troubleshooting)

---

## 1. What you need

| Item | Notes |
|---|---|
| ESP32-S3-USB-OTG board | Or any ESP32-S3 whose native USB pins reach a connector. 8 MB flash variant is what this was built on. |
| Two USB cables | One for the UART port, one for the native USB port. **Both must be data cables** — a charge-only cable produces a device that is powered but invisible. |
| `arduino-cli` | On `PATH`, or set `ARDUINO_CLI=/path/to/arduino-cli`. |
| Python 3.9+ | `pip install pyserial` for the driver. `pip install opencv-python numpy` only if you want camera verification. |

The board is powered from the UART side, so the target's USB port only has to carry
VBUS and data. Losing the target's power does not kill the keyboard.

## 2. Wire it up

```
 your machine  ──UART (CH343, COMx)──►  ESP32-S3  ──native USB──►  target
```

- **UART port** (`UART`, CH343) → your machine. Shows up as a COM port
  (`USB-Enhanced-SERIAL CH343`, `VID 1A86 PID 55D3`).
- **Native USB port** (`USB`) → the target machine. This is the keyboard.
- Bridge the board's **OTG jumper** so the native port is allowed to be a device.

Plug the native USB into a port the target's firmware already enumerates during POST —
a rear USB 2.0 port is the safest choice. Front-panel and hub ports are the usual
reason "nothing happens".

## 3. Build the firmware

One-time toolchain setup:

```sh
arduino-cli config add board_manager.additional_urls \
  https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core update-index
arduino-cli core install esp32:esp32@2.0.17
```

Then build:

```sh
python tools/fw.py build
```

`tools/fw.py` compiles `firmware/esp32kbd` for
`esp32:esp32:esp32s3usbotg` with the board's defaults — `USBMode=USB-OTG`
(TinyUSB HID) and `UploadMode=UART0` (the CH343). The core is pinned to 2.0.17
because `USBHIDKeyboard` in the 3.x core moved APIs around.

A good build prints something like:

```
Sketch uses 300697 bytes (22%) of program storage space. Maximum is 1310720 bytes.
```

Building by hand is equivalent:

```sh
arduino-cli compile --fqbn esp32:esp32:esp32s3usbotg firmware/esp32kbd
```

## 4. Flash the firmware

```sh
python tools/fw.py upload            # auto-detects the CH343 port
python tools/fw.py upload COM9       # or name it explicitly
python tools/fw.py flash             # build + upload in one go
```

Flashing resets the board over the CH343's DTR/RTS lines and writes through the ROM
loader. On success it prints `Hash of data verified.` and
`Hard resetting via RTS pin...`.

Confirm the firmware is alive:

```sh
python tools/kbd.py ping       # PONG
python tools/kbd.py id         # OK ID esp32kbd 1
python tools/kbd.py status     # started=… stopped=… suspend=… resume=…
```

## 5. Drive the target

`started` in `status` is the number of times a host has enumerated and configured
the device. **`started=0` means the target is not talking to us** — no amount of
key code tweaking will help until that becomes ≥ 1.

```sh
python tools/kbd.py press UP               # tap
python tools/kbd.py press DOWN -n 3 -d 200 # repeat
python tools/kbd.py seq TAB DOWN ENTER     # ordered sequence
python tools/kbd.py type "hello world"     # text
python tools/kbd.py hold DOWN              # press and keep held
python tools/kbd.py release DOWN
python tools/kbd.py ra                     # release everything
```

Key names: `UP DOWN LEFT RIGHT ENTER ESC TAB SPACE BACKSPACE DELETE INSERT HOME END
PGUP PGDN PLUS MINUS EQUALS LCTRL RCTRL LSHIFT RSHIFT LALT RALT CAPS F1…F12`, or a
single character (`press a`, `press 1`).

Port override: `KBD_PORT=COM9 python tools/kbd.py press UP`.

## 6. Verify with a camera

If the target has no usable video path, point any webcam at the monitor and let the
tool prove the keypress did something:

```sh
python tools/cam.py list              # find the index of the camera on the screen
CAM_INDEX=1 python tools/cam.py grab first   # one still -> shots/first.jpg
python tools/kbd.py step RIGHT        # photo -> key -> photo -> verdict
```

`step` keeps one camera session open (so auto-exposure does not read as a screen
change), subtracts each frame's mean brightness, and reports

```
RIGHT  OK K RIGHT mean=0.481 pixels=0.000%  -> no visible change
```

Anything under ~0.2 % of pixels is exposure noise; a real BIOS navigation change
shows up in the percent range.

## 7. BIOS navigation cookbook

AMI Aptio / most AMI-derived setup utilities:

| Key | Effect |
|---|---|
| `ESC` | Back one level; from a submenu it also returns focus to the menu bar |
| `LEFT` / `RIGHT` | Switch top-level tabs — **only when the menu bar has focus** |
| `UP` / `DOWN` | Move the selection in a list |
| `TAB` | Move focus between panes |
| `ENTER` | Open a submenu / activate a field |
| `+` / `-` | Change the value of the selected option |
| `F1` | General help |
| `F5` | Optimized defaults |
| `F9` | Setup defaults / previous values (board dependent) |
| `F10` | **Save changes and reset** |
| `ESC` on the top level | Exit, with an optional save prompt |

Working pattern — switch tabs, then navigate inside:

```sh
python tools/kbd.py seq ESC RIGHT RIGHT    # ESC gives the menu bar focus, then move tabs
python tools/kbd.py seq DOWN DOWN ENTER    # pick an item, open it
python tools/kbd.py press ESC              # back out unchanged
```

Practical rules:

- Run `python tools/kbd.py ra` before walking away, so no key is left held.
- Do not send `F10` unless you mean to save and reboot.
- A BIOS refreshes its `USB Devices` count only on hotplug; if you are using that as
  your success signal, expect it to lag behind what you just did.

## 8. Troubleshooting

**`started=0` and the target never sees a keyboard.**
Check, in order: (1) the native USB cable is in the target and is a *data* cable;
(2) the OTG jumper is bridged; (3) you are in a port the firmware enumerates during
POST; (4) `python tools/kbd.py vbus` — `BSesVld=1` means power is arriving,
`DEVADDR=0` with `susp=1` means the host is not driving the bus at all, which is a
cable/port problem, not a keyboard problem.

**The board keeps rebooting / the device disappears between commands.**
Opening the serial port with pyserial's default line states pulses the auto-reset
circuit. `tools/kbd.py` sets `dtr = True`, `rts = False` for exactly this reason. If
you open the port yourself, do the same; anything else either reboots the chip
(dropping the link the target already enumerated) or, worse, latches GPIO0 low.

**`rst:0x1 (POWERON), boot:0x0 (DOWNLOAD(USB/UART0))` and `waiting for download`.**
The chip came up in the ROM loader, so there is no HID device at all. Recover:

```sh
python -m esptool --port COM7 --after hard-reset run
```

**`ERROR: Could not open COM7 ... Access is denied`.**
Another process holds the port — `tools/kbd.py`, `esptool`, or a serial monitor. Close
it and retry; Windows gives no hint about which process it was.

**Reading `vbus`.**
`python tools/kbd.py vbus` returns the raw OTG registers:

```
GOTGCTL=00090000 BSesVld=1 ASesVld=0 DCTL=00000000 SOFTDISCON=0
DCFG=00100064 DEVADDR=6 DSTS=00410b06 susp=0 GINTSTS=04000020
```

- `BSesVld` — 1 means VBUS is arriving from the target.
- `SOFTDISCON` — 0 means our D+ pull-up is on, i.e. we are presenting ourselves.
- `DEVADDR` — the address the host assigned (bits 10:4 of `DCFG`); 0 means it never
  addressed us.
- `susp` — 1 means the bus is idle, so the host is not even driving SOF frames.

**Keys press but nothing moves in the BIOS.**
Usually focus, not delivery: `LEFT`/`RIGHT` do nothing while an item list has focus.
Press `ESC` first so the menu bar is focused. Confirm delivery independently by
switching tabs or opening a dialog you can see.
