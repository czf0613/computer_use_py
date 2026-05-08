import platform
from setuptools import setup, Extension

ext_modules = []

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
                include_dirs=["native_code/osx/include"],
                extra_compile_args=["-fobjc-arc"],
                extra_link_args=[
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
                    "CoreImage",
                    "-framework",
                    "ImageIO",
                ],
            )
        )
    case "Windows":
        raise RuntimeError(
            "scapkit_computer_use does not yet support Windows. Windows support is coming soon."
        )
    case unsupported:
        raise RuntimeError(f"scapkit_computer_use does not support {unsupported}.")

setup(
    ext_modules=ext_modules,
)
