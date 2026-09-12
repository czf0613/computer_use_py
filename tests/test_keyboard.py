import asyncio
import ctypes

import pytest
from scapkit_computer_use import (
    keyboard_click,
    keyboard_click_action,
    set_clipboard,
    get_clipboard,
    clipboard_paste,
)


@pytest.mark.asyncio
async def test_clipboard_round_trip():
    await set_clipboard("scapkit test string")
    assert await get_clipboard() == "scapkit test string"


@pytest.mark.asyncio
async def test_clipboard_unicode():
    await set_clipboard("你好世界 🌍")
    assert await get_clipboard() == "你好世界 🌍"


@pytest.mark.asyncio
async def test_clipboard_empty():
    await set_clipboard("")
    assert await get_clipboard() == ""


@pytest.mark.asyncio
async def test_keyboard_click_no_crash():
    await keyboard_click("a")


@pytest.mark.asyncio
async def test_keyboard_click_with_modifier():
    await keyboard_click("a", {"command"})


def test_keyboard_click_action_down_up():
    keyboard_click_action("a", "down")
    keyboard_click_action("a", "up")


def test_keyboard_click_invalid_key():
    with pytest.raises(ValueError, match="unknown key name"):
        keyboard_click_action("nonexistent_key", "down")


@pytest.mark.asyncio
async def test_keyboard_without_modifiers_clears_previous_flags():
    """Desktop integration: an unmodified key must not inherit Command."""
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cg.CGEventSourceFlagsState.argtypes = [ctypes.c_int]
    cg.CGEventSourceFlagsState.restype = ctypes.c_uint64
    cg.CGEventCreateKeyboardEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool]
    cg.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
    cg.CGEventSetFlags.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
    cg.CGEventSetFlags.restype = None
    cg.CGEventPost.argtypes = [ctypes.c_uint32, ctypes.c_void_p]
    cg.CGEventPost.restype = None
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    command_mask = 1 << 20

    try:
        await keyboard_click("a", {"command"})
        await asyncio.sleep(0.1)
        assert cg.CGEventSourceFlagsState(0) & command_mask, "Command event was not delivered"
        await keyboard_click("b")
        await asyncio.sleep(0.1)
        assert cg.CGEventSourceFlagsState(0) & command_mask == 0
    finally:
        # Also restore the system state when running against the broken extension.
        event = cg.CGEventCreateKeyboardEvent(None, 11, False)
        if event:
            cg.CGEventSetFlags(event, 0)
            cg.CGEventPost(0, event)
            cf.CFRelease(event)
