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
