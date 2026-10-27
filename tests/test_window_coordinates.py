"""Synthetic pixels and tree bounds only; these tests do not acquire a display."""

from dataclasses import replace

import pytest
import window_fakes
from window_fakes import capture, rect, semantic

from scapkit_computer_use import window
from scapkit_computer_use.window_errors import WindowError

backend = window_fakes.backend


@pytest.mark.asyncio
async def test_frame_mapping_and_tree_local_hit_test(backend):
    async with await capture() as session:
        ref = (await window.list_accessible_windows(123))[0].ref
        await session.attach_accessibility(ref, profile_id="candidate")
        frame = await session.screenshot(format="bgra")
        tree = await session.elements()
        point = frame.to_local(40, 40)
        assert (point.x, point.y) == (20, 20)
        assert frame.window_bounds.x == -200
        assert (
            await session.element_at(point, snapshot_id=tree.snapshot_id)
            is tree.elements[1].ref
        )
        assert (
            await session.element_at(20, 20, snapshot_id=tree.snapshot_id)
            is tree.elements[1].ref
        )
        assert "action" not in backend.calls
        for x, y in (
            (200, 0),
            (0, 160),
            (-1, 0),
            (float("nan"), 0),
            (0, float("inf")),
            (True, 0),
        ):
            with pytest.raises((TypeError, ValueError)):
                frame.to_local(x, y)


@pytest.mark.parametrize("scale", [1, 2, 1.25])
@pytest.mark.asyncio
async def test_fractional_scale_and_padding_are_derived_from_same_frame(backend, scale):
    backend.frame_changes = {
        "local_rect": rect(0, 0, 80, 60),
        "image_content_rect_px": rect(20, 10, 80 * scale, 60 * scale),
        "image_to_local": (1 / scale, 0, 0, 1 / scale, -20 / scale, -10 / scale),
    }
    async with await capture() as session:
        frame = await session.screenshot(format="bgra")
        point = frame.to_local(20 + 10 * scale, 10 + 20 * scale)
        assert (point.x, point.y) == pytest.approx((10, 20))
        with pytest.raises(ValueError):
            frame.to_local(19, 10)


@pytest.mark.asyncio
async def test_windows_mapping_does_not_apply_ui_scale_factor(backend):
    backend.platform = "windows"
    backend.frame_changes = {
        "window_bounds": rect(-300, 100, 200, 160),
        "local_rect": rect(0, 0, 200, 160),
        "image_to_local": (1, 0, 0, 1, 0, 0),
        "ui_scale_factor": 2,
    }
    async with await capture() as session:
        frame = await session.screenshot(format="bgra")
        assert frame.to_local(60, 40).coordinate_unit == "pixels"
        assert (frame.to_local(60, 40).x, frame.to_local(60, 40).y) == (60, 40)


@pytest.mark.asyncio
async def test_unavailable_mapping_allows_image_but_not_conversion(backend):
    backend.frame_changes = {
        "mapping_status": "unavailable",
        "local_rect": None,
        "image_content_rect_px": None,
        "image_to_local": None,
    }
    async with await capture() as session:
        frame = await session.screenshot(format="bgra")
        assert frame.data
        with pytest.raises(WindowError, match="mapping_unavailable"):
            frame.to_local(0, 0)


@pytest.mark.parametrize(
    "changes",
    [
        {"image_width": True},
        {"image_height": 0},
        {"stride": 799},
        {"data": b"bad"},
        {"state": "idle"},
        {"state": "suspended"},
        {"captured_at": 0},
        {"image_to_local": (2, 0, 0, 2, 0, 0)},
        {"image_to_local": (float("nan"), 0, 0, 0.5, 0, 0)},
        {"image_content_rect_px": rect(1, 0, 200, 160)},
        {"local_rect": rect(0, 0, 101, 80)},
        {"coordinate_unit": "pixels"},
        {"geometry_revision": True},
        {"mapping_status": "unavailable"},
        {"client_rect_local": rect(-1, 0, 100, 80)},
    ],
)
@pytest.mark.asyncio
async def test_malformed_frame_metadata_is_rejected(backend, changes):
    backend.frame_changes = changes
    async with await capture() as session:
        with pytest.raises(WindowError, match="backend_protocol_error"):
            await session.screenshot(format="bgra")


@pytest.mark.asyncio
async def test_jpeg_dimensions_are_read_from_bytes(backend):
    async with await capture() as session:
        assert (await session.screenshot()).image_width == 200
        backend.frame_changes["image_width"] = 201
        with pytest.raises(WindowError, match="backend_protocol_error"):
            await session.screenshot()
        backend.frame_changes = {"data": b"not-a-jpeg"}
        with pytest.raises(WindowError, match="backend_protocol_error"):
            await session.screenshot()


@pytest.mark.asyncio
async def test_stale_frames_and_live_geometry_changes_are_rejected(backend):
    async with await capture() as session:
        await session.attach_accessibility(
            (await window.list_accessible_windows(123))[0].ref, profile_id="candidate"
        )
        old = await session.screenshot(format="bgra")
        tree = await session.elements()
        backend.revision += 1
        with pytest.raises(WindowError, match="stale_geometry"):
            await session.element_at(old.to_local(40, 40), snapshot_id=tree.snapshot_id)
        tree = await session.elements()
        with pytest.raises(WindowError, match="stale_geometry"):
            await session.element_at(old.to_local(40, 40), snapshot_id=tree.snapshot_id)
        point = replace(old.to_local(40, 40), window_token="another-session")
        with pytest.raises(WindowError, match="stale_window"):
            await session.element_at(point, snapshot_id=tree.snapshot_id)


@pytest.mark.asyncio
async def test_overlap_and_missing_hits_never_pick_a_nearest_element(backend):
    backend.element_changes[2] = {"bounds": rect(10, 10, 30, 20)}
    async with await semantic() as session:
        tree = await session.elements()
        with pytest.raises(WindowError, match="ambiguous_element"):
            await session.element_at(20, 20, snapshot_id=tree.snapshot_id)
        with pytest.raises(WindowError, match="element_not_found"):
            await session.element_at(-1, -1, snapshot_id=tree.snapshot_id)


@pytest.mark.parametrize(
    "changes",
    [
        {"value": "must-not-leave-backend"},
        {"parent_index": 1},
        {"depth": 4},
        {"enabled": 1},
        {"actions": ["click"]},
    ],
)
@pytest.mark.asyncio
async def test_tree_privacy_ancestry_and_capability_validation(backend, changes):
    backend.element_changes[1] = changes
    async with await semantic() as session:
        with pytest.raises(WindowError, match="backend_protocol_error"):
            await session.elements()
