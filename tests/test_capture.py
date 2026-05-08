import asyncio
from pathlib import Path
import pytest
from scapkit_computer_use import (
    list_displays,
    start_capture,
    stop_capture,
    current_frame_jpg,
    current_frame_bgra,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


@pytest.mark.asyncio
async def test_capture():
    displays = list_displays()
    main = next(d for d in displays if d["is_main"])

    handle = await start_capture(main["id"])
    await asyncio.sleep(0.5)

    # BGRA frame validation
    frame = await current_frame_bgra(handle)
    assert frame is not None
    assert frame["width"] > 0
    assert frame["height"] > 0
    assert frame["bytes_per_row"] >= frame["width"] * 4
    assert len(frame["data"]) == frame["bytes_per_row"] * frame["height"]

    # JPEG validation
    jpg = await current_frame_jpg(handle)
    assert jpg is not None
    assert jpg[:2] == b"\xff\xd8"
    assert len(jpg) > 1000

    # Quality comparison
    low = await current_frame_jpg(handle, 10)
    high = await current_frame_jpg(handle, 95)
    assert low is not None and high is not None
    assert len(low) < len(high)

    # Capture 10 frames at 1s intervals and save
    DATA_DIR.mkdir(exist_ok=True)
    for i in range(10):
        data = await current_frame_jpg(handle, 80)
        assert data is not None
        (DATA_DIR / f"frame_{i:02d}.jpg").write_bytes(data)
        print(f"Saved frame_{i:02d}.jpg ({len(data)} bytes)")
        if i < 9:
            await asyncio.sleep(1)

    await stop_capture(handle)
