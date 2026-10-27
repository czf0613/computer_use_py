"""Reference routing, live incarnation checks and profile verification boundaries."""

import asyncio
import json
import pickle
from dataclasses import replace
from hashlib import sha256

import pytest
import window_fakes
from window_fakes import (
    approve_for_test,
    candidate_profile,
    capture,
    evidence_for,
    failure,
    semantic,
)

from scapkit_computer_use import window
from scapkit_computer_use import window_profiles as profiles
from scapkit_computer_use.window_errors import WindowError
from scapkit_computer_use.window_types import AccessibleWindowRef, ElementRef, WindowRef

backend = window_fakes.backend


@pytest.mark.parametrize(
    "cls", [WindowRef, AccessibleWindowRef, ElementRef, window.WindowSession]
)
def test_opaque_objects_cannot_be_directly_constructed(cls):
    with pytest.raises(TypeError):
        cls()


@pytest.mark.asyncio
async def test_candidates_are_immutable_one_use_and_loop_bound(backend):
    ref = (await window.list_windows())[0].ref
    with pytest.raises(AttributeError):
        ref._native = 123
    with pytest.raises(TypeError):
        pickle.dumps(ref)

    async def foreign_loop():
        with pytest.raises(WindowError, match="wrong_event_loop"):
            await window.open_window(ref)

    await asyncio.to_thread(lambda: asyncio.run(foreign_loop()))
    async with await window.open_window(ref) as session:
        with pytest.raises(WindowError, match="stale_window"):
            await window.open_window(ref)

        async def foreign_session():
            for operation in (session.capabilities, session.aclose):
                with pytest.raises(WindowError, match="wrong_event_loop"):
                    await operation()

        await asyncio.to_thread(lambda: asyncio.run(foreign_session()))
    assert backend.calls.count("open") == 1


@pytest.mark.asyncio
async def test_expired_candidates_and_forked_references_fail_before_open(
    backend, monkeypatch
):
    ref = (await window.list_windows())[0].ref
    object.__setattr__(ref, "_expires_at", 0)
    with pytest.raises(WindowError, match="stale_window"):
        await window.open_window(ref)
    ref = (await window.list_windows())[0].ref
    object.__setattr__(ref, "_process_id", -1)
    with pytest.raises(WindowError, match="stale_window"):
        await window.open_window(ref)
    assert "open" not in backend.calls


@pytest.mark.parametrize(
    "field,new", [("generation", 2), ("instance", "restarted-same-pid")]
)
@pytest.mark.asyncio
async def test_native_id_or_process_reuse_never_rebinds(backend, field, new):
    ref = (await window.list_windows())[0].ref
    setattr(backend, field, new)
    with pytest.raises(WindowError, match="stale_window"):
        await window.open_window(ref)
    async with await capture() as session:
        setattr(backend, field, 3 if field == "generation" else "restarted-again")
        with pytest.raises(WindowError, match="stale_window"):
            await session.screenshot()
        before = list(backend.calls)
        with pytest.raises(WindowError, match="stale_window"):
            await session.screenshot()
        assert before == backend.calls


@pytest.mark.asyncio
async def test_cross_session_elements_and_superseded_snapshots_are_rejected(
    backend, monkeypatch
):
    approve_for_test(monkeypatch, backend)
    async with (
        await semantic("fixture.v1") as first,
        await semantic("fixture.v1") as second,
    ):
        tree = await first.elements()
        with pytest.raises(WindowError, match="stale_element"):
            await second.set_value(tree.elements[1].ref, "offline")
        await first.elements()
        with pytest.raises(WindowError, match="stale_element"):
            await first.set_value(tree.elements[1].ref, "offline")
        tree = await first.elements()
        first._snapshot_expires = 0
        with pytest.raises(WindowError, match="stale_element"):
            await first.set_value(tree.elements[1].ref, "offline")
    assert backend.action_count == 0


@pytest.mark.asyncio
async def test_native_tree_changes_between_read_and_dispatch_are_not_researched(
    backend, monkeypatch
):
    approve_for_test(monkeypatch, backend)
    async with await semantic("fixture.v1") as session:
        tree = await session.elements()
        backend.epoch += 1
        with pytest.raises(WindowError, match="stale_element"):
            await session.set_value(tree.elements[1].ref, "offline")
        assert backend.action_count == 0
        assert backend.calls.count("elements") == 1


@pytest.mark.asyncio
async def test_profile_does_not_substitute_for_capture_root_bridge(
    backend, monkeypatch
):
    approve_for_test(monkeypatch, backend)
    backend.bridge = False
    async with await capture() as session:
        ref = (await window.list_accessible_windows(123))[0].ref
        with pytest.raises(WindowError, match="identity_unverifiable"):
            await session.attach_accessibility(ref, profile_id="fixture.v1")
        assert not session._has_semantics
    async with await semantic("fixture.v1") as control:
        tree = await control.elements()
        assert (await control.invoke(tree.elements[2].ref)).dispatch_state == "accepted"


def test_registration_cannot_self_assert_verification(backend):
    profile = candidate_profile()
    assert profiles.register_profile(profile).status == "not_run"
    with pytest.raises(TypeError):
        profiles.register_profile({"profile_id": "bypass", "verified": True})
    with pytest.raises(WindowError, match="background_not_verified"):
        profiles.verify_profile(profile.profile_id, evidence_for(profile))
    for fake in (True, {"verified": True}, "true"):
        with pytest.raises(TypeError):
            profiles.verify_profile(profile.profile_id, fake)
    with pytest.raises(ValueError):
        profiles.register_profile(replace(profile, application_version="other"))
    assert profiles.profile_validation(profile.profile_id).status == "not_run"


def test_verified_evidence_is_exact_and_revocation_is_permanent(backend, monkeypatch):
    profile = approve_for_test(monkeypatch, backend)
    assert profiles.profile_validation(profile.profile_id).status == "verified"
    with pytest.raises(WindowError, match="background_not_verified"):
        profiles.verify_profile(profile.profile_id, evidence_for(profile) + b" ")
    authorization = profiles._authorization(
        profile.profile_id, backend.subject, "text", "text_field", "set_value"
    )
    assert (
        profiles._validate_authorization(authorization)["profile_id"]
        == profile.profile_id
    )
    authorization["profile"]["actions"][0]["identifier"] = "another-control"
    with pytest.raises(WindowError, match="background_not_verified"):
        profiles._validate_authorization(authorization)
    profiles._revoke_profile(profile.profile_id)
    assert profiles.register_profile(profile).status == "failed"
    with pytest.raises(WindowError, match="background_not_verified"):
        profiles.verify_profile(profile.profile_id, evidence_for(profile))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda report: report["checks"].update(clipboard_unchanged="not_run"),
        lambda report: report["profile"].update(application_build="different"),
        lambda report: report.update(schema_version=True),
        lambda report: report.update(implementation_commit="invalid"),
    ],
)
def test_even_pinned_reports_must_match_schema_and_complete_scope(
    backend, monkeypatch, mutation
):
    profile = candidate_profile()
    profiles.register_profile(profile)
    report = json.loads(evidence_for(profile))
    mutation(report)
    evidence = json.dumps(report).encode()
    monkeypatch.setattr(
        profiles,
        "_APPROVED_EVIDENCE_SHA256",
        {profile.profile_id: sha256(evidence).hexdigest()},
    )
    with pytest.raises(WindowError, match="background_not_verified"):
        profiles.verify_profile(profile.profile_id, evidence)


@pytest.mark.asyncio
async def test_interference_revokes_all_sessions_using_profile(backend, monkeypatch):
    approve_for_test(monkeypatch, backend)
    async with (
        await semantic("fixture.v1") as first,
        await semantic("fixture.v1") as second,
    ):
        tree1, tree2 = await first.elements(), await second.elements()
        backend.action_result = failure("interference_detected", "accepted")
        with pytest.raises(WindowError, match="interference_detected"):
            await first.invoke(tree1.elements[2].ref)
        with pytest.raises(WindowError, match="interference_detected"):
            await second.invoke(tree2.elements[2].ref)
        assert profiles.profile_validation("fixture.v1").status == "failed"
    assert backend.action_count == 1


@pytest.mark.asyncio
async def test_revocation_before_native_dispatch_is_rechecked(backend, monkeypatch):
    approve_for_test(monkeypatch, backend)
    async with await semantic("fixture.v1") as session:
        tree = await session.elements()
        backend.before_action = lambda: profiles._revoke_profile("fixture.v1")
        with pytest.raises(WindowError, match="background_not_verified"):
            await session.invoke(tree.elements[2].ref)
        assert backend.action_count == 0
