"""The screen tool: look at the window the user is working in."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from nova.tools.base import Risk, Tool

if TYPE_CHECKING:  # nova.vision uses nova.tools' Windows helpers; importing it here would be circular
    from nova.vision.reader import ScreenReader


class LookArgs(BaseModel):
    question: str = Field(description="What the user wants to know about their screen, in their words.")


def screen_tools(reader: ScreenReader) -> list[Tool]:
    return [
        Tool(
            name="look_at_screen",
            description=(
                "Take a screenshot of the window the user is working in and answer a question about it: "
                "errors, code, documents, charts, forms, buttons. Use when the user refers to what is on "
                "their screen. The user confirms before the screenshot is taken."
            ),
            args_model=LookArgs,
            handler=lambda args: reader.look(args.question),
            describe=lambda args: "Look at your screen",
            # Screens show private things; the model alone must never decide to look.
            risk=Risk.MEDIUM,
            requires_confirmation=True,
            read_only=True,
            # What is on screen can include text written to steer NOVA.
            reads_untrusted=True,
        )
    ]
