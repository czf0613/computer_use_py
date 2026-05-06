from ._scapkit import (
    list_displays as list_displays_c,
    get_mouse_position as get_mouse_position_c,
    move_mouse as move_mouse_c,
    mouse_click,
)
from typing import cast
from .types import DisplayInfo, Point2D
from functools import lru_cache
import asyncio


@lru_cache(maxsize=2, typed=True)
def list_displays() -> list[DisplayInfo]:
    return cast(list[DisplayInfo], list_displays_c())


def get_mouse_position() -> Point2D:
    return cast(Point2D, get_mouse_position_c())


async def move_mouse(dest: Point2D, smooth: bool = True) -> None:
    if not smooth:
        move_mouse_c(dest["x"], dest["y"])
        return

    start = get_mouse_position()
    dx = dest["x"] - start["x"]
    dy = dest["y"] - start["y"]
    distance = (dx**2 + dy**2) ** 0.5

    steps = max(int(distance / 15), 5)
    interval = 0.5 / steps

    for i in range(1, steps + 1):
        t = i / steps
        ease = t * t * (3 - 2 * t)
        x = int(start["x"] + dx * ease)
        y = int(start["y"] + dy * ease)
        move_mouse_c(x, y)
        await asyncio.sleep(interval)


__all__ = [
    "DisplayInfo",
    "Point2D",
    "list_displays",
    "get_mouse_position",
    "move_mouse",
    "mouse_click",
]
