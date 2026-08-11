from trading_robot.gui_clipboard import (
    CONTROL_MASK,
    normalize_pasted_text,
    resolve_clipboard_action,
)


def test_ctrl_v_uses_windows_keycode_under_cyrillic_layout():
    assert resolve_clipboard_action(
        keysym="Cyrillic_em",
        keycode=86,
        state=CONTROL_MASK,
    ) == "paste"


def test_clipboard_shortcuts_resolve_by_physical_keycode():
    expected = {65: "select_all", 67: "copy", 86: "paste", 88: "cut"}
    for keycode, action in expected.items():
        assert resolve_clipboard_action(
            keysym="unrelated",
            keycode=keycode,
            state=CONTROL_MASK,
        ) == action


def test_shortcut_requires_control_modifier():
    assert resolve_clipboard_action(
        keysym="v",
        keycode=86,
        state=0,
    ) is None


def test_token_paste_strips_only_outer_whitespace():
    assert normalize_pasted_text(
        "  token.with-internal.value\r\n",
        strip_outer_whitespace=True,
    ) == "token.with-internal.value"
    assert normalize_pasted_text(
        "  arbitrary text  ",
        strip_outer_whitespace=False,
    ) == "  arbitrary text  "
