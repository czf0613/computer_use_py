"""Explicitly invoked real screen/system-audio acceptance; never a unit test."""

import argparse
import asyncio
import json
import math
from pathlib import Path


async def record(args):
    from scapkit_computer_use import check_permission, start_recording, stop_recording

    if not check_permission("ScreenCapture"):
        raise RuntimeError("Screen Recording permission is not granted; prepare permissions manually first")
    print(f"Recording display {args.display_id} at {args.fps} fps for {args.duration}s -> {args.output}", flush=True)
    handle = await start_recording(args.display_id, args.output, fps=args.fps, video_quality=args.video_quality)
    try:
        await asyncio.sleep(args.duration)
    finally:
        result = await stop_recording(handle)
        print(json.dumps({
            "path": str(result.path), "size_bytes": result.size_bytes,
            "duration_s": result.duration_s, "width": result.width,
            "height": result.height, "fps": result.fps,
            "MiB_per_minute": result.size_bytes / (1024 * 1024) * 60 / result.duration_s,
        }, ensure_ascii=False, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description="Record a user-prepared screen and system audio; no microphone.")
    parser.add_argument("--display-id", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--video-quality", type=float, default=0.75, help="Compression quality 0.0..1.0 (default: 0.75)")
    parser.add_argument("--duration", type=float, default=10)
    args = parser.parse_args()
    if not math.isfinite(args.duration) or args.duration <= 0:
        parser.error("--duration must be finite and positive")
    asyncio.run(record(args))


if __name__ == "__main__":
    main()
