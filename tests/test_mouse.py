from pprint import pprint
import pytest
from scapkit_computer_use import check_permission
from scapkit_computer_use.screen_capture_kit import (
    get_mouse_position,
    list_displays,
    mouse_click,
    mouse_scroll,
    move_mouse,
)
import asyncio


def test_check_permissions():
    assert check_permission("Accessibility") is True
    assert check_permission("ScreenCapture") is True


def test_get_mouse_position():
    pos = get_mouse_position()
    print()
    pprint(pos)
    assert "x" in pos and "y" in pos


@pytest.mark.asyncio
async def test_move_mouse_to_display_center():
    displays = list_displays()
    target = displays[0]
    center_x = int(target["x"] + target["width"] / 2)
    center_y = int(target["y"] + target["height"] / 2)
    print(f"\nMoving to center of display {target['id']}: ({center_x}, {center_y})")

    await move_mouse({"x": center_x, "y": center_y})

    pos = get_mouse_position()
    print(f"Final position: ({pos['x']}, {pos['y']})")
    assert abs(pos["x"] - center_x) <= 2
    assert abs(pos["y"] - center_y) <= 2


@pytest.mark.asyncio
async def test_move_mouse_instant():
    displays = list_displays()
    target = displays[0]
    center_x = int(target["x"] + target["width"] / 2)
    center_y = int(target["y"] + target["height"] / 2)

    await move_mouse({"x": center_x, "y": center_y}, smooth=False)

    pos = get_mouse_position()
    assert abs(pos["x"] - center_x) <= 2
    assert abs(pos["y"] - center_y) <= 2


@pytest.mark.asyncio
async def test_right_click_at_screen_center():
    displays = list_displays()
    target = displays[0]
    center_x = int(target["x"] + target["width"] / 2)
    center_y = int(target["y"] + target["height"] / 2)
    print(f"\nMoving to center of display {target['id']}: ({center_x}, {center_y})")

    await move_mouse({"x": center_x, "y": center_y})
    await mouse_click("right")

    pos = get_mouse_position()
    print(f"Right-clicked at: ({pos['x']}, {pos['y']})")


@pytest.mark.asyncio
async def test_mouse_scroll():
    await mouse_scroll("down", 3)
    await asyncio.sleep(1)
    await mouse_scroll("up", 3)
    await asyncio.sleep(1)
    await mouse_scroll("left", 2)
    await asyncio.sleep(1)
    await mouse_scroll("right", 2)
