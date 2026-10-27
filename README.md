# esp32-keyboard-driver

Turn an **ESP32-S3-USB-OTG** board into a USB HID keyboard for a machine you can't
reach with a normal keyboard — server BIOS setup screens, headless boxes, or a box
whose IPMI/KVM has no working USB path.

The native USB port is the keyboard the target sees. The CH343 UART port is the
control channel you drive from your own machine:

```
 [ your host ] --- UART (CH343, 115200) --- [ ESP32-S3 ] --- native USB (HID) --- [ target ]
   kbd.py                                   firmware                            BIOS / OS
```

No network, no drivers, no vendor software on the target. It enumerates as a plain
boot keyboard, so it works before any OS is running.

## Why

Built while trying to reach an ASUS/AMI Aptio setup screen over a PiKVM whose USB
and HDMI paths were dead. The ESP32-S3 route worked where the Pi 4 USB gadget and
its cabling did not: a fresh, simple, bus-powered HID device on a port the firmware
already enumerates during POST.

## Hardware

- **ESP32-S3-USB-OTG** dev board (ESP32-S3-WROOM-1, 8 MB flash). Other ESP32-S3
  boards work if the native USB pins reach a connector; adjust the FQBN.
- The board's **OTG jumper** bridged so the native USB port can be a device.
- Native USB port (`USB`) → target machine.
- UART port (`UART`, CH343) → your machine, appears as a COM port.

The board is powered from the UART side, so the target's USB port only has to carry
VBUS + data. It survives a target power cycle.

## Firmware

`firmware/esp32kbd/esp32kbd.ino` — Arduino core, no external libraries.

- USB HID boot keyboard via TinyUSB (`USBHIDKeyboard`)
- UART0 console, line protocol, every command answered with `OK …` or `ERR …`
- USB state telemetry: mount/unmount counters plus raw DWC2 registers, which is
  what made the link problems diagnosable

### Protocol

One command per line, case-insensitive, arguments after a space:

| Command | Effect |
|---|---|
| `PING` | replies `PONG` |
| `ID` | `OK ID esp32kbd 1` |
| `STATUS` | mount counters: `started=… stopped=… suspend=… resume=…` |
| `K <key>` | press + release one key |
| `H <key>` / `R <key>` | hold / release one key |
| `RA` | release everything |
| `T <text>` | type a string |
| `WAKE` | tap a modifier as a keepalive |
| `VBUS` | dump `GOTGCTL`, `DCTL`, `DCFG`, `DSTS`, `GINTSTS` |
| `RECONNECT` | soft disconnect/reconnect of the USB device |
| `CONNECT` | force the D+ pull-up on |

Key names: `UP DOWN LEFT RIGHT ENTER ESC TAB SPACE BACKSPACE DELETE INSERT HOME END
PGUP PGDN PLUS MINUS EQUALS LCTRL RCTRL LSHIFT RSHIFT LALT RALT CAPS F1…F12`, or any
single character (`K a`, `K 1`).

`STATUS` is the one that matters when nothing seems to happen:
**`started` only increments when the host has enumerated and configured the device.**
`started=0` means the target never got as far as talking to us — not a keymap problem.

### Build and flash

Requires [arduino-cli](https://arduino.github.io/arduino-cli/) and the ESP32 core:

```sh
arduino-cli config add board_manager.additional_urls \
  https://espressif.github.io/arduino-esp32/package_esp32_index.json
arduino-cli core install esp32:esp32@2.0.17

python tools/fw.py build
python tools/fw.py upload          # auto-detects the CH343 port
python tools/fw.py flash           # both
```

FQBN: `esp32:esp32:esp32s3usbotg` with the board's defaults
(`USBMode=USB-OTG`, `UploadMode=UART0`).

## Host driver

```sh
pip install pyserial
python tools/kbd.py ping
python tools/kbd.py press UP
python tools/kbd.py seq DOWN DOWN ENTER
python tools/kbd.py type "hello"
python tools/kbd.py hold DOWN
python tools/kbd.py release DOWN
python tools/kbd.py ra
python tools/kbd.py status
```

Optional camera verification (`pip install opencv-python numpy`) — points a webcam at
the screen and proves a keypress actually changed it, which is how you work when the
target has no video feed:

```sh
python tools/cam.py list           # find the camera index
python tools/kbd.py step RIGHT     # photo → key → photo → "CHANGED" / "no visible change"
```

`tools/cam.py` grabs stills (`python tools/cam.py grab name`) and can crop a region.
Set `CAM_INDEX` for a camera other than index 1.

## Gotchas that will cost you an evening

**DTR high / RTS low is the only safe way to open the serial port.** The CH343
auto-reset circuit pulses EN/GPIO0 on any other combination:

- a reset drops the USB link the target has already enumerated, and the target may
  simply never re-enumerate a port it has given up on;
- a reset with GPIO0 low drops the chip into ROM download mode
  (`rst:0x1, boot:0x0 (DOWNLOAD(USB/UART0))`), where no HID device exists at all.
  Recover with `python -m esptool --port COM7 --after hard-reset run`.

`tools/kbd.py` sets `dtr = True`, `rts = False` right after opening for exactly this
reason. Don't open the port with pyserial defaults.

**A BIOS only refreshes its "USB Devices" line on hotplug.** The count is a useful
test (`1 Keyboard` → `2 Keyboards`), but it won't update while you stare at it.

**AMI Aptio tab navigation needs menu-bar focus.** In this firmware, `LEFT`/`RIGHT`
switch tabs only when the tab bar has focus — press `ESC` first. Once focus is in an
item list, `LEFT`/`RIGHT` move within a field (e.g. date segments) and do nothing
visible on a plain list.

**`+`/`-` change values, `F10` saves and resets, `ESC` exits.** `kbd.py ra` before you
walk away so no key is left held.

## Limits

- Keyboard only — no mouse, no virtual media.
- US keymap for `T`; special characters follow the Arduino HID map.
- Not a KVM: there is no video path here on purpose.
