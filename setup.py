import os
import platform

# Source builds use the minimum version of the ScreenCaptureKit APIs we call.
# Release CI explicitly selects 15.0 and arm64 for the prebuilt wheels.
if platform.system() == "Darwin":
    deployment_target = os.environ.setdefault("MACOSX_DEPLOYMENT_TARGET", "12.3")
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
                    "ScreenCaptureKit",
                    "-framework",
                    "CoreMedia",
                    "-framework",
                    "CoreVideo",
                    "-framework",
                    "ImageIO",
                    deployment_target_arg,
                ],
            )
        )
    case "Windows":
        # Subprocess support is pure Python; desktop control is still macOS-only.
        pass
    case unsupported:
        raise RuntimeError(f"scapkit_computer_use does not support {unsupported}.")

setup(
    ext_modules=ext_modules,
)
