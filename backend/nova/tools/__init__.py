from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from nova.memory.long_term import MemoryStore
from nova.scheduler.reminders import ReminderStore
from nova.tools.base import Risk, Tool, ToolRegistry, ToolResult
from nova.tools.files import open_path, search_files
from nova.tools.memory import memory_tools
from nova.tools.reminders import reminder_tools
from nova.tools.screen import screen_tools
from nova.tools.system import close_application, open_application

if TYPE_CHECKING:
    from nova.vision.reader import ScreenReader

__all__ = ["Risk", "Tool", "ToolRegistry", "ToolResult", "build_registry"]


def build_registry(
    memory: MemoryStore,
    reminders: ReminderStore,
    on_reminders_changed: Callable[[], None],
    screen: ScreenReader | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        open_application,
        close_application,
        search_files,
        open_path,
        *memory_tools(memory),
        *reminder_tools(reminders, on_reminders_changed),
        *(screen_tools(screen) if screen else ()),
    ):
        registry.register(tool)
    return registry
