"""Candidate registration and reviewed-evidence verification for background writes.

The trust root is the installed package's reviewed SHA-256 allowlist, not a
caller-supplied boolean, digest, verifier callback, or environment variable.
No profiles have desktop acceptance evidence in this implementation stage.
"""

import json
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from threading import RLock
from types import MappingProxyType

from .window_errors import _error
from .window_types import Action, Platform, ValidationStatus, _text


@dataclass(frozen=True)
class ProfileAction:
    identifier: str
    role: str
    action: Action

    def __post_init__(self):
        _text(self.identifier, "identifier")
        _text(self.role, "role")
        if self.action not in ("set_value", "invoke"):
            raise ValueError("only set_value and invoke can be verified")


@dataclass(frozen=True)
class BackgroundProfile:
    profile_id: str
    platform: Platform
    application_id: str
    application_version: str
    application_build: str
    environment: str
    backend_build: str
    window_identifier: str
    actions: tuple[ProfileAction, ...]

    def __post_init__(self):
        if self.platform not in ("macos", "windows"):
            raise ValueError("unknown profile platform")
        for name in (
            "profile_id",
            "application_id",
            "application_version",
            "application_build",
            "environment",
            "backend_build",
            "window_identifier",
        ):
            _text(getattr(self, name), name)
        if type(self.actions) is not tuple or not 1 <= len(self.actions) <= 64:
            raise ValueError(
                "actions must be a tuple of 1..64 exact control/action rules"
            )
        if any(type(action) is not ProfileAction for action in self.actions):
            raise TypeError("actions must contain ProfileAction objects")
        if len(set(self.actions)) != len(self.actions):
            raise ValueError("duplicate profile action")


@dataclass(frozen=True)
class ProfileValidation:
    profile_id: str
    status: ValidationStatus
    evidence_sha256: str | None = None


# Adding a pin is a reviewed source change after real, explicitly authorized
# desktop acceptance. Never populate this from a file/argument/environment.
_APPROVED_EVIDENCE_SHA256 = MappingProxyType({})
_REQUIRED_CHECKS = frozenset(
    {
        "target_effect",
        "sibling_unchanged",
        "foreground_continuity",
        "pointer_continuity",
        "keyboard_continuity",
        "clipboard_unchanged",
        "forbidden_calls_zero",
        "occluded",
        "modal_rejected",
        "minimized_rejected",
        "stale_rejected",
        "cancel_cleanup",
    }
)
_lock = RLock()
_profiles: dict[str, BackgroundProfile] = {}
_validations: dict[str, ProfileValidation] = {}


def _scope(profile: BackgroundProfile) -> dict:
    scope = asdict(profile)
    scope["actions"] = list(scope["actions"])
    return scope


def register_profile(profile: BackgroundProfile) -> ProfileValidation:
    """Register an immutable candidate. Registration never enables actions.

    A profile ID cannot be rebound to another scope. Failed/revoked profiles
    cannot be reset by registration; use a new reviewed profile version.
    """
    if type(profile) is not BackgroundProfile:
        raise TypeError("profile must be a BackgroundProfile")
    with _lock:
        existing = _profiles.get(profile.profile_id)
        if existing is not None and existing != profile:
            raise ValueError("profile_id is already bound to a different scope")
        if existing is None:
            _profiles[profile.profile_id] = profile
            _validations[profile.profile_id] = ProfileValidation(
                profile.profile_id, "not_run"
            )
        return _validations[profile.profile_id]


def profile_validation(profile_id: str) -> ProfileValidation:
    """Return verification state; an unregistered ID is not_run."""
    _text(profile_id, "profile_id")
    with _lock:
        return _validations.get(profile_id, ProfileValidation(profile_id, "not_run"))


def verify_profile(profile_id: str, evidence: bytes) -> ProfileValidation:
    """Verify exact reviewed evidence bytes against the installed trust root.

    Evidence schema is documented in docs/window-backend-contract.md. This
    verifies the identity of an audited report, not the physical desktop itself.
    No approved reports are shipped yet, so production writes remain disabled.
    """
    _text(profile_id, "profile_id")
    if type(evidence) is not bytes:
        raise TypeError("evidence must be bytes")
    if not 1 <= len(evidence) <= 65536:
        raise ValueError("evidence must contain 1..65536 bytes")
    digest = sha256(evidence).hexdigest()
    with _lock:
        profile = _profiles.get(profile_id)
        previous = _validations.get(profile_id)
        if (
            profile is None
            or previous.status == "failed"
            or _APPROVED_EVIDENCE_SHA256.get(profile_id) != digest
        ):
            raise _error("background_not_verified", "verify_profile")
        try:
            report = json.loads(evidence)
            valid = (
                type(report) is dict
                and type(report.get("schema_version")) is int
                and report["schema_version"] == 1
                and report.get("profile") == _scope(profile)
                and type(report.get("checks")) is dict
                and all(
                    report["checks"].get(name) == "passed" for name in _REQUIRED_CHECKS
                )
                and re.fullmatch(
                    r"[0-9a-f]{40}", report.get("implementation_commit", "")
                )
                is not None
                and re.fullmatch(r"[0-9a-f]{64}", report.get("report_sha256", ""))
                is not None
            )
        except (ValueError, TypeError, RecursionError):
            valid = False
        if not valid:
            raise _error("background_not_verified", "verify_profile")
        result = ProfileValidation(profile_id, "verified", digest)
        _validations[profile_id] = result
        return result


def _matching_profile(profile_id: str | None, subject: dict | None):
    """Return an immutable profile only for a verified exact runtime subject."""
    with _lock:
        profile = _profiles.get(profile_id)
        validation = _validations.get(profile_id)
        if profile is None or validation is None or validation.status != "verified":
            return None
        expected = _scope(profile)
        del expected["actions"], expected["profile_id"]
        if subject != expected:
            return None
        return profile


def _authorization(
    profile_id: str | None,
    subject: dict | None,
    identifier: str | None,
    role: str,
    action: str,
) -> dict:
    with _lock:
        profile = _matching_profile(profile_id, subject)
        if (
            profile is None
            or not identifier
            or ProfileAction(identifier, role, action) not in profile.actions
        ):
            raise _error("background_not_verified", action)
        return {
            "profile": _scope(profile),
            "evidence_sha256": _validations[profile_id].evidence_sha256,
        }


def _validate_authorization(authorization: dict) -> dict:
    """Native entry calls this with attached Python thread state, before locks.

    Return a fresh scope after matching the live registry, or raise. Native
    copies it into native-only storage then rechecks every field against the
    target immediately before dispatch. See the backend contract.
    """
    if type(authorization) is not dict or set(authorization) != {
        "profile",
        "evidence_sha256",
    }:
        raise _error("background_not_verified", "authorize")
    scope = authorization["profile"]
    if type(scope) is not dict or type(scope.get("profile_id")) is not str:
        raise _error("background_not_verified", "authorize")
    with _lock:
        profile = _profiles.get(scope["profile_id"])
        validation = _validations.get(scope["profile_id"])
        if (
            profile is None
            or validation.status != "verified"
            or validation.evidence_sha256 != authorization["evidence_sha256"]
            or scope != _scope(profile)
        ):
            raise _error("background_not_verified", "authorize")
        return _scope(profile)


def _revoke_profile(profile_id: str | None) -> None:
    with _lock:
        if profile_id in _profiles:
            _validations[profile_id] = ProfileValidation(profile_id, "failed")
