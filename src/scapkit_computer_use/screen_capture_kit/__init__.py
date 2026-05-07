from ._scapkit import (
    list_displays as list_displays_c,
    get_mouse_position as get_mouse_position_c,
    move_mouse as move_mouse_c,
    mouse_click as mouse_click_action,
    keyboard_click as _keyboard_click_c,
)
from typing import cast, Literal
from .types import DisplayInfo, Point2D
from .keys import KEY_CODES, MODIFIER_FLAGS, MODIFIER
from functools import lru_cache
import asyncio
from asyncio import subprocess


def _resolve_key(key: str) -> int:
    try:
        return KEY_CODES[key]
    except KeyError:
        raise ValueError(
            f"unknown key name '{key}'. See screen_capture_kit.keys.KEY_CODES for valid names."
        )


def _resolve_flags(modifiers: list[MODIFIER] | None) -> int:
    flags = 0
    for mod in modifiers or []:
        flags |= MODIFIER_FLAGS[mod]
    return flags


@lru_cache(maxsize=2, typed=True)
def list_displays() -> list[DisplayInfo]:
    return cast(list[DisplayInfo], list_displays_c())


def get_mouse_position() -> Point2D:
    return cast(Point2D, get_mouse_position_c())


async def move_mouse(
    dest: Point2D, smooth: bool = True, duration_s: float = 0.5
) -> None:
    if not smooth:
        move_mouse_c(dest["x"], dest["y"])
        return

    start = get_mouse_position()
    dx = dest["x"] - start["x"]
    dy = dest["y"] - start["y"]
    distance = (dx**2 + dy**2) ** 0.5

    steps = max(int(distance / 15), 5)
    interval = duration_s / steps

    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)
        x = int(start["x"] + dx * ease)
        y = int(start["y"] + dy * ease)
        move_mouse_c(x, y)
        await asyncio.sleep(interval)


async def mouse_long_click(key: Literal["left", "right"], duration_s: float) -> None:
    assert duration_s > 0

    mouse_click_action(key, "down")
    await asyncio.sleep(duration_s)
    mouse_click_action(key, "up")


async def mouse_click(key: Literal["left", "right"]) -> None:
    await mouse_long_click(key, 0.03)


async def mouse_drag(dest: Point2D) -> None:
    mouse_click_action("left", "down")
    await asyncio.sleep(0.05)
    await move_mouse(dest)
    await asyncio.sleep(0.05)
    mouse_click_action("left", "up")


def keyboard_click_action(
    key: str,
    action: Literal["down", "up"],
    modifiers: list[MODIFIER] | None = None,
) -> None:
    _keyboard_click_c(_resolve_key(key), action, _resolve_flags(modifiers))


async def keyboard_click(
    key: str,
    modifiers: list[MODIFIER] | None = None,
) -> None:
    flags = _resolve_flags(modifiers)
    code = _resolve_key(key)
    _keyboard_click_c(code, "down", flags)
    await asyncio.sleep(0.01)
    _keyboard_click_c(code, "up", flags)


async def key_combo(key: str, modifiers: list[MODIFIER]) -> None:
    await keyboard_click(key, modifiers)


async def set_clipboard(text: str) -> None:
    proc = await subprocess.create_subprocess_exec(
        "pbcopy",
        stdin=subprocess.PIPE,
    )
    await proc.communicate(input=text.encode("utf-8"))


async def get_clipboard() -> str:
    proc = await subprocess.create_subprocess_exec(
        "pbpaste",
        stdout=subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    return stdout.decode("utf-8")


async def clipboard_paste() -> None:
    await keyboard_click("v", ["command"])
