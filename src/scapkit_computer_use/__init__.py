from .screen_capture_kit import (
    list_displays,
    get_mouse_position,
    move_mouse,
    mouse_click,
)
from .screen_capture_kit.types import DisplayInfo, Point2D

__all__ = [
    "DisplayInfo",
    "Point2D",
    "list_displays",
    "get_mouse_position",
    "move_mouse",
    "mouse_click",
]
