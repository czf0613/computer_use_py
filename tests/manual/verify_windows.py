"""Opt-in Windows acceptance using the real backend and a disposable Tk window.

Run only with an unlocked, prepared desktop and permission to move the cursor,
capture both displays and record the default system audio endpoint.
uv run python tests/manual/verify_windows.py --desktop --round-trips 20
"""
import argparse
import asyncio
import ctypes as c
from ctypes import wintypes as w
import json
import io
import math
from pathlib import Path
import struct
import sys
import threading
import time
import traceback
import wave


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--desktop", action="store_true")
    parser.add_argument("--round-trips", type=int, default=20)
    parser.add_argument("--audio-tone", action="store_true",
                        help="Play a short generated tone and verify recorded audio is non-silent")
    parser.add_argument("--skip-recording", action="store_true",
                        help="Only test input and save both display screenshots")
    parser.add_argument("--output", type=Path, default=Path("build/windows_research/acceptance.json"))
    args = parser.parse_args()
    if sys.platform != "win32" or not args.desktop:
        parser.error("Windows and explicit --desktop authorization are required")
    import tkinter as tk
    import scapkit_computer_use as lib
    from scapkit_computer_use.screen_capture_kit import _scapkit as native

    u = c.WinDLL("user32", use_last_error=True)
    u.SetThreadDpiAwarenessContext.argtypes = [c.c_void_p]
    u.SetThreadDpiAwarenessContext.restype = c.c_void_p
    old_dpi = u.SetThreadDpiAwarenessContext(c.c_void_p(-4))
    u.GetAncestor.argtypes = [w.HWND, w.UINT]
    u.GetAncestor.restype = w.HWND
    u.GetWindowRect.argtypes = [w.HWND, c.POINTER(w.RECT)]
    u.GetWindowRect.restype = w.BOOL
    u.SetWindowPos.argtypes = [w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT]
    u.SetWindowPos.restype = w.BOOL
    u.GetDpiForWindow.argtypes = [w.HWND]
    u.GetDpiForWindow.restype = w.UINT
    u.GetAsyncKeyState.argtypes = [c.c_int]
    u.GetAsyncKeyState.restype = c.c_short
    u.ClientToScreen.argtypes = [w.HWND, c.POINTER(w.POINT)]
    u.ClientToScreen.restype = w.BOOL
    ds = lib.list_displays()
    if len(ds) != 2:
        raise RuntimeError("This acceptance run requires exactly two displays")
    ds.sort(key=lambda d: not d["is_main"])
    root = tk.Tk()
    root.title("Scapkit Windows backend acceptance")
    root.geometry("720x420")
    root.attributes("-topmost", True)
    tk.Label(root, text="Scapkit cross-display drag test", font=("Segoe UI", 20)).pack(pady=35)
    tk.Label(root, text="This window will move between both displays.").pack()
    entry = tk.Entry(root, font=("Segoe UI", 16))
    entry.pack(pady=30, padx=30, fill="x")
    events = []
    button_events = []
    key_events = []
    root.bind("<MouseWheel>", lambda event: events.append({"wheel": event.delta}))
    root.bind("<ButtonPress>", lambda event: button_events.append((event.num, "down")))
    root.bind("<ButtonRelease>", lambda event: button_events.append((event.num, "up")))
    root.bind("<KeyPress>", lambda event: key_events.append((event.keysym, "down")))
    root.bind("<KeyRelease>", lambda event: key_events.append((event.keysym, "up")))
    root.update()
    hwnd = u.GetAncestor(root.winfo_id(), 2)
    # Initial placement only. All test movement below goes through scapkit.
    u.SetWindowPos(hwnd, None, ds[0]["x"]+400, ds[0]["y"]+400, 720, 420, 0x0014)
    root.lift()
    entry.focus_force()
    report = {"displays": ds, "drags": [], "screenshots": [], "errors": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)

    def rect():
        value = w.RECT()
        if not u.GetWindowRect(hwnd, c.byref(value)):
            raise c.WinError(c.get_last_error())
        return [value.left, value.top, value.right, value.bottom]

    def in_display(point, display):
        return (display["x"] <= point[0] < display["x"]+display["width"] and
                display["y"] <= point[1] < display["y"]+display["height"])

    async def tests():
        u.SetThreadDpiAwarenessContext(c.c_void_p(-4))
        try:
            # Verify application receipt, not just a successful SendInput return.
            await lib.move_mouse({"x": entry.winfo_rootx()+entry.winfo_width()//2,
                                  "y": entry.winfo_rooty()+entry.winfo_height()//2}, smooth=False)
            for button, number in (("left", 1), ("right", 3)):
                button_events.clear()
                await lib.mouse_click(button)
                await asyncio.sleep(0.1)
                assert button_events == [(number, "down"), (number, "up")], (button, button_events)
                assert not u.GetAsyncKeyState(1 if number == 1 else 2) & 0x8000
            report["left_and_right_click"] = "passed (application received down/up; buttons released)"
            entry.focus_force()
            key_events.clear()
            # Letter keysyms can become VK_PROCESSKEY while an IME is active.
            # Use a navigation key without changing the user's input method.
            await lib.keyboard_click("left")
            await asyncio.sleep(0.1)
            assert ("Left", "down") in key_events and ("Left", "up") in key_events, key_events
            report["key_down_and_up"] = "passed (application received Left down/up)"
            root.after(0, lambda: (entry.delete(0, "end"), entry.insert(0, "scapkit"), entry.focus_force()))
            await asyncio.sleep(0.15)
            await lib.key_combo("a", {"control"})
            await lib.keyboard_click("delete")
            await asyncio.sleep(0.1)
            assert entry.get() == "", "Ctrl+A / Backspace did not reach the test entry"
            assert not u.GetAsyncKeyState(0x11) & 0x8000, "Ctrl remains pressed"
            original = await lib.get_clipboard()
            try:
                text = "Windows 跨屏测试 🌍"
                await lib.set_clipboard(text)
                assert await lib.get_clipboard() == text
                await lib.clipboard_paste()
                await asyncio.sleep(0.1)
                assert entry.get() == text
            finally:
                await lib.set_clipboard(original)
            report["keyboard_and_unicode_clipboard"] = "passed (original clipboard text restored)"
            before = rect()
            await lib.move_mouse({"x": before[0]+200, "y": before[1]+100}, smooth=False)
            for direction, expected in (("up", -120), ("down", 120)):
                events.clear()
                await lib.mouse_scroll(direction, 1)
                await asyncio.sleep(0.1)
                assert events and events[-1]["wheel"] == expected, (direction, events)
            report["vertical_scroll_events"] = "passed"
            drag = asyncio.create_task(lib.mouse_drag({"x": before[0]+300, "y": before[1]+200}))
            await asyncio.sleep(0.1)
            drag.cancel()
            try:
                await drag
            except asyncio.CancelledError:
                pass
            await asyncio.sleep(0.05)
            assert not u.GetAsyncKeyState(1) & 0x8000, "Cancelled drag left the button down"
            report["cancelled_drag"] = "passed"
            for iteration in range(args.round_trips):
                for target in (ds[1], ds[0]):
                    before = rect()
                    start = {"x": before[0]+130, "y": before[1]+int(15*u.GetDpiForWindow(hwnd)/96)}
                    destination = {"x": target["x"]+target["width"]//2,
                                   "y": target["y"]+target["height"]//2}
                    await lib.move_mouse(start, smooth=False)
                    await lib.mouse_drag(destination)
                    await asyncio.sleep(0.15)
                    after = rect()
                    center = ((after[0]+after[2])//2, (after[1]+after[3])//2)
                    assert in_display(center, target), ("Window did not cross displays", before, after, target)
                    assert not u.GetAsyncKeyState(1) & 0x8000, "Left button remains down"
                    report["drags"].append({"from": before, "to": after, "dpi": u.GetDpiForWindow(hwnd), "target": target["id"]})
                print(f"Cross-display round trip {iteration+1}/{args.round_trips}: passed", flush=True)
            handles = []
            try:
                for display in ds:
                    handles.append(await lib.start_capture(display["id"]))
                for display, handle in zip(ds, handles):
                    for _ in range(100):
                        pixels = await lib.current_frame_bgra(handle)
                        if pixels:
                            break
                        await asyncio.sleep(0.05)
                    assert pixels and (pixels["width"], pixels["height"]) == (display["width"], display["height"])
                    jpeg = await lib.current_frame_jpg(handle)
                    assert jpeg.startswith(b"\xff\xd8")
                    assert len(pixels["data"]) == pixels["bytes_per_row"] * pixels["height"]
                    stem = f"{args.output.stem}-display-{display['id']}"
                    jpg_path = args.output.with_name(stem + ".jpg").absolute()
                    bgra_path = args.output.with_name(stem + ".bgra").absolute()
                    jpg_path.write_bytes(jpeg)
                    bgra_path.write_bytes(pixels["data"])
                    report["screenshots"].append({"display": display["id"],
                        "width": pixels["width"], "height": pixels["height"],
                        "bytes_per_row": pixels["bytes_per_row"], "bgra_bytes": len(pixels["data"]),
                        "jpeg_bytes": len(jpeg), "jpeg_path": str(jpg_path), "bgra_path": str(bgra_path)})
            finally:
                for handle in handles:
                    await lib.stop_capture(handle)
            if args.skip_recording:
                return
            output = args.output.with_name(f"desktop-{time.time_ns()}.mp4").absolute()
            handle = await lib.start_recording(ds[0]["id"], output, fps=30)
            try:
                if args.audio_tone:
                    import winsound
                    stream = io.BytesIO()
                    with wave.open(stream, "wb") as wav:
                        wav.setparams((2, 2, 48000, 0, "NONE", "not compressed"))
                        wav.writeframes(b"".join(struct.pack("<hh", value, value)
                            for i in range(48000)
                            for value in [int(3000 * math.sin(i * 2 * math.pi * 440 / 48000))]))
                    await asyncio.sleep(0.4)
                    await asyncio.to_thread(winsound.PlaySound, stream.getvalue(), winsound.SND_MEMORY)
                    await asyncio.sleep(1.6)
                else:
                    await asyncio.sleep(3)
            finally:
                result = await lib.stop_recording(handle)
            report["recording"] = {**vars(result), "path": str(result.path)}
            if hasattr(native, "_test_decode"):
                report["decoded"] = await asyncio.to_thread(native._test_decode, str(output))
                if args.audio_tone:
                    assert report["decoded"]["audio_peak"] > 100, "Loopback recording is unexpectedly silent"
        except BaseException:
            report["errors"].append(traceback.format_exc())
        finally:
            args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            print(json.dumps({"drags": len(report["drags"]), "screenshots": report["screenshots"], "errors": report["errors"]}, ensure_ascii=False), flush=True)
            root.after(0, root.destroy)

    worker = threading.Thread(target=lambda: asyncio.run(tests()))
    root.after(300, worker.start)
    root.mainloop()
    worker.join()
    u.SetThreadDpiAwarenessContext(old_dpi)
    if report["errors"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
