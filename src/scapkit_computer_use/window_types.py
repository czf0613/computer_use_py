"""Immutable window DTOs. Coordinates are local points (macOS) or pixels (Windows)."""

from dataclasses import dataclass, field
from math import isfinite
from typing import Literal

from .window_errors import DispatchState, _error

Platform = Literal["macos", "windows"]
CoordinateUnit = Literal["points", "pixels"]
APISupport = Literal["supported", "unsupported", "unknown"]
IdentityStatus = Literal["verified", "unverified", "stale"]
ValidationStatus = Literal["verified", "not_run", "failed"]
Action = Literal["set_value", "invoke"]


def _integer(value, name: str, minimum: int, maximum: int) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside {minimum}..{maximum}")
    return value


def _number(value, name: str) -> float:
    if type(value) not in (int, float):
        raise TypeError(f"{name} must be a number")
    try:
        value = float(value)
    except OverflowError:
        raise ValueError(f"{name} must be finite") from None
    if not isfinite(value) or abs(value) > 1e12:
        raise ValueError(f"{name} must be finite and bounded")
    return value


def _text(value, name: str, limit: int = 4096, *, empty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a string")
    if (not empty and not value) or "\0" in value or len(value.encode("utf-8")) > limit:
        raise ValueError(f"invalid {name}")
    return value


class _OpaqueRef:
    __slots__ = (
        "_backend",
        "_expires_at",
        "_info",
        "_loop",
        "_native",
        "_owner",
        "_process_id",
        "_snapshot_id",
        "_token",
        "_used",
    )

    def __new__(cls, *args, **kwargs):
        raise TypeError(f"{cls.__name__} is issued by the window API")

    def __setattr__(self, name, value):
        raise AttributeError("window references are immutable")

    def __reduce__(self):
        raise TypeError("window references cannot be serialized")

    @property
    def token(self) -> str:
        return self._token

    def __repr__(self) -> str:
        return f"<{type(self).__name__}>"


class WindowRef(_OpaqueRef):
    """One-use, loop-bound capture candidate; not a native window ID."""

    __slots__ = ()


class AccessibleWindowRef(_OpaqueRef):
    """One-use semantic root, independent of a capture window."""

    __slots__ = ()


class ElementRef(_OpaqueRef):
    """Element owned by exactly one session and its latest tree snapshot."""

    __slots__ = ()


@dataclass(frozen=True)
class WindowRect:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        for name in ("x", "y", "width", "height"):
            object.__setattr__(self, name, _number(getattr(self, name), name))
        if self.width <= 0 or self.height <= 0:
            raise ValueError("rectangle dimensions must be positive")

    def contains(self, x: float, y: float) -> bool:
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height


@dataclass(frozen=True)
class WindowPoint:
    x: float
    y: float
    window_token: str
    generation: int
    geometry_revision: int
    coordinate_unit: CoordinateUnit


@dataclass(frozen=True)
class WindowInfo:
    ref: WindowRef
    platform: Platform
    native_id: str
    pid: int
    process_instance: str
    generation: int
    title: str | None
    bounds: WindowRect
    coordinate_unit: CoordinateUnit
    visible: bool | None
    minimized: bool | None
    capture_support: APISupport
    inspect_support: APISupport


@dataclass(frozen=True)
class AccessibleWindowInfo:
    ref: AccessibleWindowRef
    platform: Platform
    native_id: str
    pid: int
    process_instance: str
    generation: int
    title: str | None
    bounds: WindowRect
    coordinate_unit: CoordinateUnit
    visible: bool | None
    minimized: bool | None
    capture_support: APISupport
    inspect_support: APISupport


@dataclass(frozen=True)
class WindowCapability:
    api_support: APISupport
    identity_status: IdentityStatus
    background_validation: ValidationStatus
    reason_code: str | None
    profile_id: str | None
    application_version: str | None


@dataclass(frozen=True)
class WindowCapabilities:
    capture: WindowCapability
    inspect_elements: WindowCapability
    set_value: WindowCapability
    invoke: WindowCapability
    coordinate_input: WindowCapability


@dataclass(frozen=True)
class WindowFrame:
    window_token: str
    generation: int
    frame_id: str
    geometry_revision: int
    captured_at: float
    state: Literal["complete"]
    format: Literal["jpeg", "bgra"]
    data: bytes = field(repr=False)
    image_width: int
    image_height: int
    stride: int | None
    window_bounds: WindowRect
    local_rect: WindowRect | None
    image_content_rect_px: WindowRect | None
    client_rect_local: WindowRect | None
    coordinate_unit: CoordinateUnit
    image_to_local: tuple[float, float, float, float, float, float] | None
    mapping_status: Literal["verified", "unavailable"]

    def to_local(self, image_x: float, image_y: float) -> WindowPoint:
        """Map an original image pixel to local units, retaining frame provenance.

        This is arithmetic, not input. Pass the resulting point to element_at;
        that method checks the session, geometry and live tree before lookup.
        """
        x, y = _number(image_x, "image_x"), _number(image_y, "image_y")
        if self.mapping_status != "verified" or self.image_to_local is None:
            raise _error(
                "mapping_unavailable", "to_local", window_token=self.window_token
            )
        if (
            self.image_content_rect_px is None
            or not self.image_content_rect_px.contains(x, y)
        ):
            raise ValueError("point is outside image content")
        a, b, c, d, tx, ty = self.image_to_local
        return WindowPoint(
            a * x + c * y + tx,
            b * x + d * y + ty,
            self.window_token,
            self.generation,
            self.geometry_revision,
            self.coordinate_unit,
        )


@dataclass(frozen=True)
class ElementInfo:
    ref: ElementRef
    identifier: str | None
    role: str
    name: str | None
    bounds: WindowRect | None
    enabled: bool | None
    editable: bool | None
    password: bool | None
    actions: tuple[Action, ...]
    depth: int
    parent_index: int | None


@dataclass(frozen=True)
class ElementSnapshot:
    window_token: str
    snapshot_id: str
    generation: int
    tree_epoch: int
    geometry_revision: int
    coordinate_unit: CoordinateUnit
    mapping_status: Literal["verified", "unavailable"]
    elements: tuple[ElementInfo, ...]


@dataclass(frozen=True)
class ActionReceipt:
    operation_id: str
    dispatch_state: DispatchState
    effect_state: Literal["verified", "unknown"]
