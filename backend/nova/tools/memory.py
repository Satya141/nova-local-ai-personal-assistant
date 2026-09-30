"""Tools for the user's long-term memory: remember, recall, forget."""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel, Field

from nova.memory.long_term import CATEGORIES, MemoryStore, SensitiveContent, keywords
from nova.tools.base import Risk, Tool, ToolResult

# A recall query made only of these asks for everything, not for a topic.
_BROAD_WORDS = {"everything", "anything", "all", "know", "remember", "user", "about", "things", "stuff"}

Category = Literal["profile", "preference", "project", "person", "routine", "fact"]
assert set(Category.__args__) == set(CATEGORIES)


class RememberArgs(BaseModel):
    content: str = Field(description="One short sentence about the user, e.g. 'The user prefers dark mode.'")
    category: Category = Field(
        default="fact",
        description="profile (who they are), preference, project, person (someone in their life), routine, or fact.",
    )


class RecallArgs(BaseModel):
    query: str = Field(description="What to look for, e.g. 'projects' or 'sister'. Use 'everything' for all of it.")


class ForgetArgs(BaseModel):
    memory_id: int = Field(description="The memory's number: the #id shown in what you remember, or from recall.")


def _listing(memories) -> list[dict]:
    return [{"id": m.id, "category": m.category, "content": m.content, "saved": m.updated_at[:10]} for m in memories]


def memory_tools(store: MemoryStore) -> list[Tool]:
    async def remember(args: RememberArgs) -> ToolResult:
        try:
            saved = await store.add(args.content, args.category, "explicit")
        except SensitiveContent:
            return ToolResult(
                False,
                "Refused: NOVA never stores passwords, codes, keys or ID numbers. Tell the user it was not saved.",
            )
        except ValueError as exc:
            return ToolResult(False, str(exc))
        return ToolResult(
            True, json.dumps({"saved": saved.memory.content, "id": saved.memory.id, "updated_existing": not saved.created})
        )

    async def recall(args: RecallArgs) -> ToolResult:
        if keywords(args.query) <= _BROAD_WORDS:
            found = store.all(limit=25)
        else:
            found = [memory for memory, _ in await store.search(args.query, limit=8)]
        if not found:
            return ToolResult(True, json.dumps({"memories": [], "note": "Nothing is stored about that."}))
        return ToolResult(True, json.dumps({"memories": _listing(found)}))

    async def forget(args: ForgetArgs) -> ToolResult:
        memory = store.get(args.memory_id)
        if memory is None:
            return ToolResult(False, f"There is no memory with id {args.memory_id}. Use recall to find the right one.")
        store.delete(args.memory_id)
        return ToolResult(True, json.dumps({"forgot": memory.content}))

    def describe_forget(args: ForgetArgs) -> str:
        memory = store.get(args.memory_id)
        return f"Forget: {memory.content}" if memory else f"Forget memory #{args.memory_id}"

    return [
        Tool(
            name="remember",
            description="Save a fact about the user to long-term memory. Use when the user asks you to remember something.",
            args_model=RememberArgs,
            handler=remember,
            describe=lambda args: f"Remember: {args.content}",
        ),
        Tool(
            name="recall",
            description="Search long-term memory for what you know about the user. Returns memories with their ids.",
            args_model=RecallArgs,
            handler=recall,
            describe=lambda args: f"Look through memories for '{args.query}'",
        ),
        Tool(
            name="forget",
            description=(
                "Delete one memory by its #id, from what you remember or from recall. "
                "The user confirms before it is deleted."
            ),
            args_model=ForgetArgs,
            handler=forget,
            describe=describe_forget,
            risk=Risk.MEDIUM,
            requires_confirmation=True,
        ),
    ]
