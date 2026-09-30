from collections.abc import Callable

from nova.memory.long_term import MemoryStore
from nova.scheduler.reminders import ReminderStore
from nova.tools.base import Risk, Tool, ToolRegistry, ToolResult
from nova.tools.files import open_path, search_files
from nova.tools.memory import memory_tools
from nova.tools.reminders import reminder_tools
from nova.tools.system import close_application, open_application

__all__ = ["Risk", "Tool", "ToolRegistry", "ToolResult", "build_registry"]


def build_registry(
    memory: MemoryStore, reminders: ReminderStore, on_reminders_changed: Callable[[], None]
) -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        open_application,
        close_application,
        search_files,
        open_path,
        *memory_tools(memory),
        *reminder_tools(reminders, on_reminders_changed),
    ):
        registry.register(tool)
    return registry
