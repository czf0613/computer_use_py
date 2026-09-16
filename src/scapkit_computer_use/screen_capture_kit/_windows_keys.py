"""Win32 virtual-key names and backend-local modifier masks."""

MODIFIER_FLAGS = {"control": 1, "shift": 2, "alt": 4, "option": 4,
                  "win": 8, "command": 8}
KEY_CODES = {chr(c): c for c in range(ord("A"), ord("Z") + 1)}
KEY_CODES = {key.lower(): value for key, value in KEY_CODES.items()}
KEY_CODES.update({str(n): 0x30 + n for n in range(10)})
KEY_CODES.update({f"f{n}": 0x6F + n for n in range(1, 25)})
KEY_CODES.update({f"numpad_{n}": 0x60 + n for n in range(10)})
KEY_CODES.update({
    "return": 0x0D, "enter": 0x0D, "tab": 0x09, "space": 0x20,
    "delete": 0x08, "backspace": 0x08, "forward_delete": 0x2E,
    "escape": 0x1B, "capslock": 0x14, "insert": 0x2D,
    "command": 0x5B, "win": 0x5B, "right_win": 0x5C,
    "shift": 0x10, "right_shift": 0xA1,
    "control": 0x11, "right_control": 0xA3,
    "alt": 0x12, "option": 0x12, "right_alt": 0xA5, "right_option": 0xA5,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD,
    ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE,
    "/": 0xBF, "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
    "numlock": 0x90, "numpad_multiply": 0x6A, "numpad_add": 0x6B,
    "numpad_subtract": 0x6D, "numpad_decimal": 0x6E, "numpad_divide": 0x6F,
})
