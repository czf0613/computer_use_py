from pprint import pprint
from scapkit_computer_use import list_displays


def test_list_displays_returns_list():
    displays = list_displays()
    assert isinstance(displays, list)
    assert len(displays) > 0


def test_display_entry_has_required_keys():
    displays = list_displays()
    for d in displays:
        assert "id" in d
        assert "x" in d
        assert "y" in d
        assert "width" in d
        assert "height" in d
        assert "scale_factor" in d
        assert "is_main" in d


def test_display_dimensions_are_positive():
    displays = list_displays()
    for d in displays:
        assert d["width"] > 0
        assert d["height"] > 0


def test_exactly_one_main_display():
    displays = list_displays()
    main_count = sum(1 for d in displays if d["is_main"])
    assert main_count == 1


def test_display_values():
    displays = list_displays()
    print()
    pprint(displays)
