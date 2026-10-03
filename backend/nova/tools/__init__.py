from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nova.memory.long_term import MemoryStore
from nova.scheduler.reminders import ReminderStore
from nova.tools.base import Risk, Tool, ToolRegistry, ToolResult
from nova.tools.file_ops import FILE_TOOLS
from nova.tools.files import open_path, search_files
from nova.tools.memory import memory_tools
from nova.tools.reminders import reminder_tools
from nova.tools.screen import screen_tools
from nova.tools.system import close_application, open_application
from nova.tools.timeline import timeline_tools
from nova.tools.web import web_tools

if TYPE_CHECKING:
    from nova.apps.control import AppControl
    from nova.browser.session import BrowserSession
    from nova.timeline import Timeline
    from nova.vision.reader import ScreenReader

__all__ = ["Risk", "Tool", "ToolRegistry", "ToolResult", "build_registry"]


def build_registry(
    memory: MemoryStore,
    reminders: ReminderStore,
    on_reminders_changed: Callable[[], None],
    screen: ScreenReader | None = None,
    browser: BrowserSession | None = None,
    timeline: Timeline | None = None,
    apps: AppControl | None = None,
) -> ToolRegistry:
    from nova.tools.apps import app_tools  # here, not at the top: nova.apps imports nova.tools._windows

    registry = ToolRegistry()
    for tool in (
        open_application,
        close_application,
        search_files,
        open_path,
        *FILE_TOOLS,
        *memory_tools(memory),
        *reminder_tools(reminders, on_reminders_changed),
        *(screen_tools(screen) if screen else ()),
        *(web_tools(browser) if browser else ()),
        *(timeline_tools(timeline) if timeline else ()),
        *(app_tools(apps) if apps else ()),
    ):
        registry.register(tool)
    return registry
