/*
 * esp32kbd - USB HID keyboard for driving the AILONG server BIOS.
 *
 * Target:  ESP32-S3-USB-OTG board, OTG jumper bridged.
 *   - native USB port (GPIO19/20) -> server, enumerates as a boot keyboard
 *   - UART0 (GPIO43/44) via CH343 -> operator console at 115200
 *
 * Line protocol (newline terminated, case-insensitive):
 *   PING              -> PONG
 *   ID                -> OK ID esp32kbd 1
 *   K <key>           press+release one key
 *   H <key>           hold a key
 *   R <key>           release a key
 *   RA                release everything
 *   T <text>          type a text string
 *   WAKE              send a few harmless modifier taps (host keepalive)
 *
 * Every command answers "OK ..." or "ERR ..." so the caller can wait for it.
 */

#include "USB.h"
#include "USBHIDKeyboard.h"

USBHIDKeyboard Keyboard;

#define FW_ID "esp32kbd 1"

struct KeyMap {
  const char *name;
  uint8_t code;
};

// Names accepted by K/H/R. Single characters (letters, digits) are handled
// separately, so they are not listed here.
static const KeyMap KEYMAP[] = {
  {"LEFT",      KEY_LEFT_ARROW},
  {"RIGHT",     KEY_RIGHT_ARROW},
  {"UP",        KEY_UP_ARROW},
  {"DOWN",      KEY_DOWN_ARROW},
  {"ENTER",     KEY_RETURN},
  {"ESC",       KEY_ESC},
  {"ESCAPE",    KEY_ESC},
  {"TAB",       KEY_TAB},
  {"SPACE",     ' '},
  {"BACKSPACE", KEY_BACKSPACE},
  {"BS",        KEY_BACKSPACE},
  {"DELETE",    KEY_DELETE},
  {"DEL",       KEY_DELETE},
  {"INSERT",    KEY_INSERT},
  {"INS",       KEY_INSERT},
  {"HOME",      KEY_HOME},
  {"END",       KEY_END},
  {"PGUP",      KEY_PAGE_UP},
  {"PAGEUP",    KEY_PAGE_UP},
  {"PGDN",      KEY_PAGE_DOWN},
  {"PAGEDOWN",  KEY_PAGE_DOWN},
  {"PLUS",      '+'},
  {"MINUS",     '-'},
  {"EQUALS",    '='},
  {"LCTRL",     KEY_LEFT_CTRL},
  {"RCTRL",     KEY_RIGHT_CTRL},
  {"LSHIFT",    KEY_LEFT_SHIFT},
  {"RSHIFT",    KEY_RIGHT_SHIFT},
  {"LALT",      KEY_LEFT_ALT},
  {"RALT",      KEY_RIGHT_ALT},
  {"CAPS",      KEY_CAPS_LOCK},
  {"F1",  KEY_F1},  {"F2",  KEY_F2},  {"F3",  KEY_F3},  {"F4",  KEY_F4},
  {"F5",  KEY_F5},  {"F6",  KEY_F6},  {"F7",  KEY_F7},  {"F8",  KEY_F8},
  {"F9",  KEY_F9},  {"F10", KEY_F10}, {"F11", KEY_F11}, {"F12", KEY_F12},
};

static const size_t KEYMAP_N = sizeof(KEYMAP) / sizeof(KEYMAP[0]);

static char line[192];
static size_t line_len = 0;

static void upcase(char *s) {
  for (; *s; s++) {
    if (*s >= 'a' && *s <= 'z') *s = (char)(*s - 'a' + 'A');
  }
}

static char *trim(char *s) {
  while (*s == ' ' || *s == '\t') s++;
  char *e = s + strlen(s);
  while (e > s && (e[-1] == ' ' || e[-1] == '\t' || e[-1] == '\r' || e[-1] == '\n')) *--e = 0;
  return s;
}

// Returns the keycode for a name, or 0 if unknown.
static uint8_t lookup_key(const char *raw) {
  char name[24];
  strncpy(name, raw, sizeof(name) - 1);
  name[sizeof(name) - 1] = 0;
  char *n = trim(name);
  upcase(n);

  if (n[0] && !n[1]) return (uint8_t)n[0];      // single character: 'A', '1', '-'
  for (size_t i = 0; i < KEYMAP_N; i++) {
    if (!strcmp(n, KEYMAP[i].name)) return KEYMAP[i].code;
  }
  return 0;
}

static void release_all() {
  Keyboard.releaseAll();
}
// --- USB link telemetry -----------------------------------------------------
// The STARTED event fires when the host has enumerated and configured us, so it
// is the definitive "the server can see this keyboard" signal.
static volatile uint32_t ev_started = 0, ev_stopped = 0, ev_suspend = 0, ev_resume = 0;

static void on_usb_event(void *arg, esp_event_base_t base, int32_t id, void *data) {
  switch (id) {
    case ARDUINO_USB_STARTED_EVENT: ev_started++; Serial.println("EV USB_STARTED"); break;
    case ARDUINO_USB_STOPPED_EVENT: ev_stopped++; Serial.println("EV USB_STOPPED"); break;
    case ARDUINO_USB_SUSPEND_EVENT: ev_suspend++; Serial.println("EV USB_SUSPEND"); break;
    case ARDUINO_USB_RESUME_EVENT:  ev_resume++;  Serial.println("EV USB_RESUME");  break;
    default: break;
  }
}


static void handle(char *raw) {
  char *cmd = trim(raw);
  if (!*cmd) return;

  char *arg = cmd;
  while (*arg && *arg != ' ') arg++;
  if (*arg) { *arg = 0; arg++; }
  arg = trim(arg);
  upcase(cmd);

  if (!strcmp(cmd, "PING")) { Serial.println("PONG"); return; }
  if (!strcmp(cmd, "ID"))   { Serial.print("OK ID "); Serial.println(FW_ID); return; }
  if (!strcmp(cmd, "STATUS")) {
    Serial.print("OK STATUS started="); Serial.print((unsigned long)ev_started);
    Serial.print(" stopped=");           Serial.print((unsigned long)ev_stopped);
    Serial.print(" suspend=");           Serial.print((unsigned long)ev_suspend);
    Serial.print(" resume=");            Serial.println((unsigned long)ev_resume);
    return;
  }
  if (!strcmp(cmd, "VBUS")) {
    // ESP32-S3 USB-OTG (DWC2) block.
    uint32_t gotg = *(volatile uint32_t *)0x60080000UL;
    uint32_t dcfg = *(volatile uint32_t *)0x60080400UL;
    uint32_t dctl = *(volatile uint32_t *)0x60080804UL;
    uint32_t dsts = *(volatile uint32_t *)0x60080808UL;
    uint32_t gint = *(volatile uint32_t *)0x60080014UL;
    Serial.printf("OK VBUS GOTGCTL=%08lx BSesVld=%lu ASesVld=%lu DCTL=%08lx SOFTDISCON=%lu "
                  "DCFG=%08lx DEVADDR=%lu DSTS=%08lx susp=%lu GINTSTS=%08lx\r\n",
                  (unsigned long)gotg, (unsigned long)((gotg >> 19) & 1),
                  (unsigned long)((gotg >> 18) & 1), (unsigned long)dctl,
                  (unsigned long)((dctl >> 1) & 1), (unsigned long)dcfg,
                  (unsigned long)((dcfg >> 4) & 0x7f), (unsigned long)dsts,
                  (unsigned long)(dsts & 1), (unsigned long)gint);
    return;
  }
  if (!strcmp(cmd, "RECONNECT")) {
    // Soft disconnect then reconnect: forces the host to re-enumerate us.
    volatile uint32_t *dctl = (volatile uint32_t *)0x60080804UL;
    *dctl |= 2UL;
    delay(400);
    *dctl &= ~2UL;
    delay(50);
    Serial.printf("OK RECONNECT DCTL=%08lx\r\n", (unsigned long)*dctl);
    return;
  }
  if (!strcmp(cmd, "CONNECT")) {
    // Force the D+ pull-up on (clear SOFTDISCON) if the DCD never connected.
    volatile uint32_t *dctl = (volatile uint32_t *)0x60080804UL;
    *dctl = *dctl & ~2UL;
    delay(10);
    Serial.printf("OK CONNECT DCTL=%08lx\r\n", (unsigned long)*dctl);
    return;
  }
  if (!strcmp(cmd, "RA"))   { release_all(); Serial.println("OK RA"); return; }
  if (!strcmp(cmd, "WAKE")) {
    Keyboard.press(KEY_LEFT_SHIFT); delay(20);
    Keyboard.release(KEY_LEFT_SHIFT);
    Serial.println("OK WAKE");
    return;
  }

  if (!strcmp(cmd, "T")) {
    if (!*arg) { Serial.println("ERR empty text"); return; }
    Keyboard.print(arg);
    Serial.println("OK T");
    return;
  }

  if (!strcmp(cmd, "K") || !strcmp(cmd, "H") || !strcmp(cmd, "R")) {
    uint8_t code = lookup_key(arg);
    if (!code) {
      Serial.print("ERR unknown key '");
      Serial.print(arg);
      Serial.println("'");
      return;
    }
    if (!strcmp(cmd, "K")) {
      Keyboard.press(code);
      delay(25);
      Keyboard.release(code);
      Serial.print("OK K ");
    } else if (!strcmp(cmd, "H")) {
      Keyboard.press(code);
      Serial.print("OK H ");
    } else {
      Keyboard.release(code);
      Serial.print("OK R ");
    }
    Serial.println(arg);
    return;
  }

  Serial.print("ERR unknown command '");
  Serial.print(cmd);
  Serial.println("'");
}

void setup() {
  Serial.begin(115200);
  delay(200);

  USB.onEvent(on_usb_event);
  Keyboard.begin();
  USB.begin();

  Serial.println();
  Serial.print("READY ");
  Serial.println(FW_ID);
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (line_len) {
        line[line_len] = 0;
        handle(line);
        line_len = 0;
      }
    } else if (line_len < sizeof(line) - 1) {
      line[line_len++] = c;
    }
  }
}
