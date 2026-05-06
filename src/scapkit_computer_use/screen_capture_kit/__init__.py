from ._scapkit import list_displays as list_displays_c
from typing import cast
from .types import DisplayInfo


def list_displays() -> list[DisplayInfo]:
    return cast(list[DisplayInfo], list_displays_c())


__all__ = ["DisplayInfo", "list_displays"]
