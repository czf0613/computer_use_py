"""Fault injection at native transport boundaries; no desktop IPC or input."""
import json
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS native backend")
from scapkit_computer_use.screen_capture_kit import _scapkit as native

if not hasattr(native, "_test_window_ax_transport"):
    pytest.skip("requires SCAPKIT_TESTING build", allow_module_level=True)


def probe_tree(mode, depth=8, count=512):
    result = native._test_window_ax_transport(mode, depth, count)
    assert result["ok"], result
    return result["value"]


@pytest.mark.parametrize("mode", ["probe_stop_fail", "probe_remove_fail", "probe_both_fail", "probe_success", "probe_late_success"])
def test_capture_cleanup_transport_effects(mode):
    # Each failed teardown must keep its gate/owner alive. A fresh interpreter
    # isolates cases without a test reset of the production cleanup state.
    code = """
import json, sys, sysconfig
from scapkit_computer_use.screen_capture_kit import _scapkit as native
if sysconfig.get_config_var('Py_GIL_DISABLED'):
    assert not sys._is_gil_enabled()
result = native._test_window_frame(sys.argv[1])
if sysconfig.get_config_var('Py_GIL_DISABLED'):
    assert not sys._is_gil_enabled()
print(json.dumps(result))
"""
    child = subprocess.run([sys.executable, "-I", "-c", code, mode], env={}, capture_output=True, text=True, timeout=15)
    assert child.returncode == 0, child.stderr
    envelope = json.loads(child.stdout)
    assert envelope["ok"], envelope
    result = envelope["value"]
    failure = mode.endswith("fail")
    assert result["early_cleanup"] is False
    assert result["source_running"] == (mode in ("probe_stop_fail", "probe_both_fail"))
    assert result["output_detached"] == (mode not in ("probe_remove_fail", "probe_both_fail"))
    assert result["close_cleanup"] == (not failure), result
    assert result["repeat_cleanup"] == (not failure), result
    assert result["finished"] == (not failure), result
    assert result["stream_released"] == (not failure), result
    assert result["flight_retained"] == failure, result
    assert result["capture_gate_busy"] == failure, result
    if failure:
        assert result["owner_alive"], result


@pytest.mark.parametrize("mode", ["children_count_error", "children_values_error", "children_invalid"])
def test_ax_children_transport_failure_rejects_snapshot(mode):
    result = probe_tree(mode)
    assert not result["snapshot_ok"], result
    assert not result["validate_ok"], result
    assert result["error"]["code"] == "stale_element"


@pytest.mark.parametrize("mode", ["validation_count_error", "validation_values_error"])
def test_ax_children_failure_after_snapshot_rejects_validation(mode):
    result = probe_tree(mode)
    assert result["snapshot_ok"] and result["node_count"] == 2, result
    assert not result["validate_ok"], result


@pytest.mark.parametrize("mode", ["middle_geometry", "middle_object", "middle_identifier", "middle_enabled", "middle_editable", "middle_actions"])
def test_ax_middle_observation_must_match_retained_fingerprint(mode):
    result = probe_tree(mode)
    assert not result["snapshot_ok"], result
    assert not result["validate_ok"], result
    assert result["error"]["code"] == "stale_element"


@pytest.mark.parametrize("mode,depth,count,nodes", [
    ("stable", 8, 512, 2), ("leaf_unsupported", 8, 512, 2),
    ("children_count_error", 0, 512, 1), ("children_values_error", 8, 1, 1),
])
def test_ax_stable_leaf_and_requested_truncation(mode, depth, count, nodes):
    result = probe_tree(mode, depth, count)
    assert result["snapshot_ok"] and result["validate_ok"], result
    assert result["node_count"] == nodes
    if nodes == 2:
        assert result["snapshot_child_x"] == result["fingerprint_child_x"] == 10
