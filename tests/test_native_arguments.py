"""Safe native boundary regressions, isolated from pytest's process.

Input-event tests deliberately use invalid enum strings even when testing another
argument. The original unchecked parser therefore cannot reach event posting.
Do not add movement calls here: a parser regression could move the real cursor.
"""

import subprocess
import sys
import textwrap

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS extension")


def run_native_check(source):
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "faulthandler",
            "-c",
            textwrap.dedent(
                """
                import resource
                resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
                from scapkit_computer_use.screen_capture_kit import _scapkit
                """
            )
            + textwrap.dedent(source),
        ],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=20,
    )
    assert result.returncode == 0, (
        f"native child exited with {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


@pytest.mark.parametrize(
    ("call", "error"),
    [
        ("check_permission()", "TypeError"),
        ("check_permission(None)", "TypeError"),
        ("check_permission(object())", "TypeError"),
        ("check_permission(b'Accessibility')", "TypeError"),
        ("check_permission('invalid', 'extra')", "TypeError"),
        (r"check_permission('invalid\x00permission')", "ValueError"),
        (r"check_permission('\ud800')", "UnicodeEncodeError"),
        ("mouse_click('invalid', None)", "TypeError"),
        ("mouse_click('invalid', b'down')", "TypeError"),
        (r"mouse_click('invalid', '\ud800')", "UnicodeEncodeError"),
        ("mouse_scroll('invalid', None)", "TypeError"),
        ("mouse_scroll('invalid', 1.5)", "TypeError"),
        ("mouse_scroll('invalid', 2**100)", "OverflowError"),
        ("mouse_scroll('invalid', -(2**100))", "OverflowError"),
        ("keyboard_click(0, 'invalid', None)", "TypeError"),
        ("keyboard_click(0, 'invalid', 1.5)", "TypeError"),
        ("keyboard_click(0, 'invalid', -1)", "OverflowError"),
        ("keyboard_click(0, 'invalid', 2**64)", "OverflowError"),
        ("list_displays(None)", "TypeError"),
        ("get_mouse_position(None)", "TypeError"),
    ],
)
def test_native_argument_errors_do_not_crash_or_overwrite_exception(call, error):
    run_native_check(
        f"""
        try:
            _scapkit.{call}
        except {error}:
            pass
        else:
            raise AssertionError('expected {error}')
        """
    )


@pytest.mark.parametrize("key_code", [-1, 65536])
def test_key_code_is_checked_before_narrowing(key_code):
    # Invalid action is an independent barrier against posting in older builds.
    run_native_check(
        f"""
        try:
            _scapkit.keyboard_click({key_code}, 'invalid')
        except ValueError as exc:
            assert 'key_code' in str(exc), str(exc)
        else:
            raise AssertionError('expected invalid key_code to be rejected')
        """
    )


@pytest.mark.parametrize(
    "call",
    [
        "check_permission('invalid')",
        "mouse_click('invalid', 'down')",
        "mouse_click('left', 'invalid')",
        "mouse_scroll('invalid', 0)",
        "keyboard_click(0, 'invalid')",
        "keyboard_click(65535, 'invalid', 2**64 - 1)",
    ],
)
def test_invalid_enums_remain_value_errors(call):
    run_native_check(
        f"""
        try:
            _scapkit.{call}
        except ValueError:
            pass
        else:
            raise AssertionError('expected ValueError')
        """
    )


@pytest.mark.parametrize(
    "call",
    [
        "mouse_scroll('invalid', BrokenIndex())",
        "keyboard_click(0, 'invalid', BrokenIndex())",
    ],
)
def test_integer_conversion_preserves_user_exception(call):
    run_native_check(
        f"""
        class BrokenIndex:
            def __index__(self):
                raise RuntimeError('index conversion failed')

        try:
            _scapkit.{call}
        except RuntimeError as exc:
            assert str(exc) == 'index conversion failed'
        else:
            raise AssertionError('expected __index__ exception')
        """
    )


def test_invalid_calls_keep_exceptions_separate_across_threads():
    run_native_check(
        """
        from concurrent.futures import ThreadPoolExecutor

        def check(_):
            for _ in range(100):
                try:
                    _scapkit.mouse_click('invalid', None)
                except TypeError:
                    pass
                else:
                    raise AssertionError('expected TypeError')
                try:
                    _scapkit.keyboard_click(0, 'invalid', -1)
                except OverflowError:
                    pass
                else:
                    raise AssertionError('expected OverflowError')

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(check, range(16)))
        """
    )
