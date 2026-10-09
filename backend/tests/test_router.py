"""The tool router: which tools the model is shown for a request."""

from __future__ import annotations

import json
import pathlib
import re
import tempfile
from unittest.mock import MagicMock

import pytest

from nova.database import Database
from nova.integrations.connections import not_connected_tools
from nova.memory.long_term import MemoryStore
from nova.scheduler.reminders import ReminderStore
from nova.tools import build_registry
from nova.tools.router import GROUPS, tools_for


@pytest.fixture(scope="module")
def registry():
    db = Database(pathlib.Path(tempfile.mkdtemp()) / "router.db")
    tools = build_registry(
        MemoryStore(db, None), ReminderStore(db), lambda: None,
        screen=MagicMock(), browser=MagicMock(), timeline=MagicMock(), apps=MagicMock(),
    )
    for tool in (tool for group in not_connected_tools() for tool in group):
        tools.register(tool)
    return tools


def offered(registry, text, earlier=()):
    return tools_for(text, list(earlier), registry.names())


# What a person might say, and a tool the model must be able to reach for it.
NEEDS = [
    ("Remind me tomorrow at 7 to go running", "create_reminder"),
    ("every weekday at 9 remind me to stretch", "create_reminder"),
    ("in 20 minutes remind me to call mom", "create_reminder"),
    ("every morning at 8, search the web for AI news and give me a short summary", "schedule_task"),
    ("every morning at 8, search the web for AI news and give me a short summary", "web_search"),
    ("what reminders do I have", "list_reminders"),
    ("cancel my reminder about the dentist", "cancel_reminder"),
    ("Find my resume and open it", "search_files"),
    ("Find my resume and open it", "open_path"),
    ("sort my Downloads into folders by type", "sort_files"),
    ("sort my Downloads into folders by type", "list_folder"),
    ("delete the old invoices", "delete_paths"),
    ("rename budget.xlsx to budget-2026.xlsx", "rename_path"),
    ("move the pdfs to my Documents folder", "move_paths"),
    ("create a folder called Taxes", "create_folder"),
    ("open VS Code", "open_application"),
    ("close Notepad", "close_application"),
    ("open whatsapp and message ravi kumar hi", "read_app"),
    ("open whatsapp and message ravi kumar hi", "type_in_app"),
    ("any unread email?", "email_search"),
    ("any unread email?", "email_not_connected"),
    ("reply to Priya and say Friday works", "email_draft"),
    ("what's on my calendar tomorrow?", "calendar_events"),
    ("put the dentist on my calendar at 3", "calendar_add"),
    ("is anything waiting for me on GitHub?", "github_activity"),
    ("comment on that issue", "github_comment"),
    ("what's the latest Python version?", "web_search"),
    ("open python.org and find the release notes", "open_web_page"),
    ("open python.org and find the release notes", "read_web_page"),
    ("what's wrong with this code on my screen?", "look_at_screen"),
    ("remember that my passport is in the blue drawer", "remember"),
    ("forget that", "forget"),
    ("what do you know about me?", "recall"),
    ("what did I do yesterday?", "recall_activity"),
    ("when did I last open VS Code?", "recall_activity"),
    ("find the budget spreadsheet, open it, and remind me tomorrow at 10 to review it", "search_files"),
    ("find the budget spreadsheet, open it, and remind me tomorrow at 10 to review it", "create_reminder"),
]


@pytest.mark.parametrize(("text", "tool"), NEEDS)
def test_the_tool_a_request_needs_is_offered(registry, text, tool):
    chosen = offered(registry, text)
    assert chosen is None or tool in chosen


def test_every_name_in_a_group_is_a_real_tool(registry):
    # A name that is not a tool is a typo: the model would never be shown the tool it meant.
    # Account tools exist only while connected, so the names come from their definitions.
    defined = set(registry.names())
    for path in pathlib.Path(__file__).parent.parent.joinpath("nova", "tools").glob("*.py"):
        defined |= set(re.findall(r'^\s+name="(\w+)",', path.read_text(encoding="utf-8"), re.MULTILINE))
    for group in GROUPS:
        for name in group.tools:
            assert name in defined, f"{group.name} lists {name}, which is not a tool"


def test_every_registered_tool_is_in_a_group_or_always_offered(registry):
    # Nothing to check beyond the rule itself: ungrouped tools are offered on every routed request.
    chosen = tools_for("Remind me tomorrow", [], registry.names())
    grouped = {name for group in GROUPS for name in group.tools}
    assert {name for name in registry.names() if name not in grouped} <= chosen


def test_a_clear_request_offers_far_fewer_tools(registry):
    chosen = offered(registry, "Remind me tomorrow at 7 to go running")
    everything = len(json.dumps(registry.schemas()))
    shown = len(json.dumps(registry.schemas(only=chosen)))
    assert shown < everything * 0.6


def test_an_unclear_request_offers_every_tool(registry):
    assert offered(registry, "hello there") is None
    assert offered(registry, "hmm") is None


def test_a_short_follow_up_is_read_with_the_request_before_it(registry):
    chosen = offered(registry, "yes do it", earlier=["any unread email?"])
    assert chosen is not None
    assert "email_search" in chosen


def test_a_long_message_is_not_read_with_an_earlier_one(registry):
    chosen = offered(registry, "what is the weather going to be like in Hyderabad this weekend", earlier=["any unread email?"])
    assert chosen is not None
    assert "web_search" in chosen
    assert "email_search" not in chosen


def test_a_tool_in_no_group_is_always_offered():
    chosen = tools_for("Remind me tomorrow", [], ["create_reminder", "brand_new_tool"])
    assert chosen is not None
    assert "brand_new_tool" in chosen


def test_the_registry_only_filters_it_never_adds(registry):
    names = {schema["function"]["name"] for schema in registry.schemas(only={"remember", "not_a_tool"})}
    assert names == {"remember"}


def test_a_request_from_a_phone_still_hides_tools_that_need_the_pc(registry):
    names = {schema["function"]["name"] for schema in registry.schemas(remote=True, only={"look_at_screen", "remember"})}
    assert "look_at_screen" not in names
    assert "remember" in names
