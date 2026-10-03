"""Using the user's own apps: reading their windows' controls, clicking, typing and pressing keys,
through Windows UI Automation. See `control.py`."""

from nova.apps.control import AppControl, AppError

__all__ = ["AppControl", "AppError"]
