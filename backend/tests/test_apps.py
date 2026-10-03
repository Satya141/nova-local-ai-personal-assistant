"""Working in the user's apps: which windows are off limits, what asks first, which keys exist.

These never touch a real window. (A scratch run against a real Notepad once typed into and
emptied the user's restored, unsaved note: real-app checks belong in files NOVA creates.)
"""

from __future__ import annotations

import pytest

from nova.apps.control import (
    CONSEQUENTIAL_KEYS,
    KEYS,
    Element,
    blocked_reason,
    find_window,
    needs_confirmation_to_click,
)
from nova.tools._windows import Window
from nova.tools.apps import KeysArgs


@pytest.mark.parametrize(
    ("process", "title"),
    [
        ("cmd", "Command Prompt"), ("powershell", "Windows PowerShell"), ("WindowsTerminal", "Terminal"),
        ("SecHealthUI", "Windows Security"), ("consent", "User Account Control"), ("regedit", "Registry Editor"),
        ("chrome", "Gmail - Google Chrome"), ("msedge", "New tab - Microsoft Edge"), ("nova", "NOVA"),
        ("ApplicationFrameHost", "Windows Security"), ("1Password", "1Password"),
    ],
)
def test_terminals_security_browsers_and_password_managers_are_off_limits(process, title):
    assert blocked_reason(process, title)


@pytest.mark.parametrize(("process", "title"), [("WhatsApp.Root", "WhatsApp"), ("notepad", "notes - Notepad"), ("SystemSettings", "Settings")])
def test_ordinary_apps_may_be_used(process, title):
    assert blocked_reason(process, title) is None


def test_the_window_the_user_means_is_found_by_title_or_program():
    windows = [Window(1, "WhatsApp", "WhatsApp.Root"), Window(2, "notes - Notepad", "notepad"), Window(3, "Settings", "SystemSettings")]
    assert find_window("whatsapp", windows).hwnd == 1
    assert find_window("Notepad", windows).hwnd == 2
    assert find_window("settings", windows).hwnd == 3
    assert find_window("excel", windows) is None and find_window("", windows) is None


@pytest.mark.parametrize(
    ("kind", "name", "asks"),
    [
        ("button", "Send", True), ("button", "Delete chat", True), ("button", "Yes", True), ("button", "Save", True),
        ("button", "Pay now", True), ("checkbox", "Bluetooth", True), ("option", "Dark", True),
        ("button", "Chats", False), ("tab", "Display", False), ("item", "Ananditha", False), ("menu item", "View", False),
    ],
)
def test_clicks_that_act_or_change_a_setting_ask_first(kind, name, asks):
    assert needs_confirmation_to_click(Element(1, kind, name)) is asks
    assert needs_confirmation_to_click(None), "an unknown control asks"


def test_only_listed_keys_can_be_pressed_and_the_risky_ones_ask():
    assert {"Enter", "Delete", "Backspace", "Ctrl+X", "Ctrl+S"} <= CONSEQUENTIAL_KEYS <= set(KEYS)
    with pytest.raises(ValueError):
        KeysArgs(keys="Win+R")  # Run box: arbitrary commands
    with pytest.raises(ValueError):
        KeysArgs(keys="Alt+F4")
    assert KeysArgs(keys="Ctrl+A").keys == "Ctrl+A"


def test_a_password_box_never_shows_its_value():
    assert "hunter2" not in Element(3, "box", "Password", value="hunter2", password=True).line()


def test_only_controls_really_on_screen_are_listed():
    """WhatsApp's window carries the Edge engine's hidden buttons ("App available. Install"); the
    model typed into them. They have no size, or lie outside the window."""
    from types import SimpleNamespace as Rect

    from nova.apps.control import _visible

    def rect(left, top, right, bottom):
        return Rect(left=left, top=top, right=right, bottom=bottom, width=lambda: right - left, height=lambda: bottom - top)

    window = rect(0, 0, 1000, 800)
    assert _visible(rect(10, 10, 200, 40), window)
    assert not _visible(rect(0, 0, 0, 0), window), "no size"
    assert not _visible(rect(1200, 10, 1300, 40), window), "outside the window"


@pytest.mark.parametrize(
    ("text", "said", "yes"),
    [
        ("Ravi Kumar", "send hi to ravi kumar on whatsapp", True),
        ("hi", "send hi to ravi", True),
        ("hi", "this is it", False),  # inside another word
        ("transfer 500", "send hi to ravi", False),
        ("", "anything", False),
        ("Hello, Priya!", "send 'hello, priya!' to priya", True),
    ],
)
def test_the_users_own_words_are_recognised_exactly(text, said, yes):
    from nova.permissions.gate import said_by_user

    assert said_by_user(text, said) is yes
