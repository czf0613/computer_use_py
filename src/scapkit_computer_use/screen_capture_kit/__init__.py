from ._scapkit import (
    list_displays as list_displays_c,
    get_mouse_position as get_mouse_position_c,
    move_mouse as move_mouse_c,
    mouse_click as mouse_click_action,
)
from typing import cast, Literal
from .types import DisplayInfo, Point2D
from functools import lru_cache
import asyncio


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
