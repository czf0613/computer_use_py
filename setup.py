import platform
from setuptools import setup

match platform.system():
    case "Darwin":
        pass
    case "Windows":
        raise RuntimeError(
            "scapkit_computer_use does not yet support Windows. Windows support is coming soon."
        )
    case unsupported:
        raise RuntimeError(f"scapkit_computer_use does not support {unsupported}.")

setup()
