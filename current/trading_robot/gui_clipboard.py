from __future__ import annotations

CONTROL_MASK = 0x0004

# Windows virtual-key codes are stable across keyboard layouts.  In particular,
# the physical V key still has keycode 86 when the active layout is Russian and
# Tk reports a Cyrillic keysym instead of ``v``.
_WINDOWS_KEYCODE_ACTIONS = {
    65: "select_all",  # A
    67: "copy",        # C
    86: "paste",       # V
    88: "cut",         # X
}
_KEYSYM_ACTIONS = {
    "a": "select_all",
    "c": "copy",
    "v": "paste",
    "x": "cut",
}


def resolve_clipboard_action(
    *,
    keysym: str | None,
    keycode: int | str | None,
    state: int | str | None,
) -> str | None:
    """Resolve Ctrl+A/C/V/X independent of the active keyboard layout.

    Tk's default ``<Control-v>`` binding may not fire under a Cyrillic layout,
    because the reported keysym is no longer ``v``.  On Windows the virtual key
    code still identifies the physical key, so it is used first.
    """
    try:
        state_value = int(state or 0)
    except (TypeError, ValueError):
        state_value = 0
    if not state_value & CONTROL_MASK:
        return None

    try:
        code = int(keycode) if keycode is not None else -1
    except (TypeError, ValueError):
        code = -1
    if code in _WINDOWS_KEYCODE_ACTIONS:
        return _WINDOWS_KEYCODE_ACTIONS[code]

    key = str(keysym or "").lower()
    return _KEYSYM_ACTIONS.get(key)


def normalize_pasted_text(text: str, *, strip_outer_whitespace: bool) -> str:
    """Normalize clipboard text before inserting it into an entry widget."""
    return text.strip() if strip_outer_whitespace else text
