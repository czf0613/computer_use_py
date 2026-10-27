import os
import platform
import sysconfig

# Source builds use the library's minimum supported macOS version.
# Release CI explicitly selects 15.0 and arm64 for the prebuilt wheels.
if platform.system() == "Darwin":
    deployment_target = os.environ.setdefault("MACOSX_DEPLOYMENT_TARGET", "13.0")
    deployment_target_arg = f"-mmacosx-version-min={deployment_target}"

from setuptools import setup, Extension

ext_modules = []
define_macros = [("PY_SSIZE_T_CLEAN", None)]
if os.environ.get("SCAPKIT_TESTING") == "1":
    define_macros.append(("SCAPKIT_TESTING", "1"))

match platform.system():
    case "Darwin":
        ext_modules.append(
            Extension(
                "scapkit_computer_use.screen_capture_kit._scapkit",
                sources=[
                    "native_code/osx/src/ext.c",
                    "native_code/osx/src/display.c",
                    "native_code/osx/src/control.c",
                    "native_code/osx/src/capture.m",
                    "native_code/osx/src/recording.m",
                    "native_code/osx/src/recording_writer.m",
                    "native_code/osx/src/window.m",
                    "native_code/osx/src/window_capture.m",
                    "native_code/osx/src/accessibility.m",
                ],
                define_macros=define_macros,
                include_dirs=["native_code/osx/include"],
                extra_compile_args=["-fobjc-arc", deployment_target_arg],
                extra_link_args=[
                    "-framework",
                    "Foundation",
                    "-framework",
                    "UniformTypeIdentifiers",
                    "-framework",
                    "CoreGraphics",
                    "-framework",
                    "ApplicationServices",
                    "-framework",
                    "Security",
                    "-framework",
                    "ScreenCaptureKit",
                    "-framework",
                    "CoreMedia",
                    "-framework",
                    "CoreVideo",
                    "-framework",
                    "ImageIO",
                    "-framework",
                    "VideoToolbox",
                    "-framework",
                    "AVFoundation",
                    "-framework",
                    "AudioToolbox",
                    deployment_target_arg,
                ],
            )
        )
    case "Windows":
        from hashlib import sha256
        from pathlib import Path

        windows_root = Path(__file__).parent / "native_code/windows"
        window_build = sha256()
        for source in sorted(windows_root.rglob("*")):
            if source.suffix in {".cpp", ".h"}:
                window_build.update(source.relative_to(windows_root).as_posix().encode())
                window_build.update(source.read_bytes())
        if sysconfig.get_config_var("Py_GIL_DISABLED"):
            define_macros.append(("Py_GIL_DISABLED", "1"))
        ext_modules.append(
            Extension(
                "scapkit_computer_use.screen_capture_kit._scapkit",
                sources=[
                    "native_code/windows/src/module.cpp",
                    "native_code/windows/src/input.cpp",
                    "native_code/windows/src/capture.cpp",
                    "native_code/windows/src/recording.cpp",
                    "native_code/windows/src/window.cpp",
                    "native_code/windows/src/window_runtime.cpp",
                    "native_code/windows/src/accessibility.cpp",
                    "native_code/windows/src/window_testing.cpp",
                ],
                include_dirs=["native_code/windows/include"],
                define_macros=define_macros + [
                    ("SCAPKIT_WINDOW_BACKEND_SHA256", '"' + window_build.hexdigest() + '"'),
                    ("NOMINMAX", "1"), ("WIN32_LEAN_AND_MEAN", "1"),
                    ("_WIN32_WINNT", "0x0A00"), ("UNICODE", "1"), ("_UNICODE", "1"),
                ],
                language="c++",
                extra_compile_args=["/std:c++20", "/EHsc", "/W4", "/utf-8"]
                + (["/analyze"] if os.environ.get("SCAPKIT_ANALYZE") == "1" else []),
                libraries=["windowsapp", "d3d11", "dxgi", "windowscodecs", "user32",
                           "gdi32", "ole32", "oleaut32", "shcore", "mfplat", "mfreadwrite",
                           "mfuuid", "propsys", "uuid", "delayimp", "dwmapi", "bcrypt", "uiautomationcore"],
                extra_link_args=["/DELAYLOAD:mfplat.dll", "/DELAYLOAD:mfreadwrite.dll"],
            )
        )
    case unsupported:
        raise RuntimeError(f"scapkit_computer_use does not support {unsupported}.")

setup(
    ext_modules=ext_modules,
)
