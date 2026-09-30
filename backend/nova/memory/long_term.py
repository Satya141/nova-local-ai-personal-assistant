"""Long-term memory: durable facts about the user, found again by meaning.

Memories are short third-person sentences ("The user is building NOVA").
Each is stored with an embedding so it can be retrieved by meaning; when no
embedding model is available, retrieval falls back to keyword overlap, so
memory degrades instead of failing.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date

from nova.database import Database, timestamp
from nova.dates import resolve_weekdays
from nova.memory.embeddings import Embedder, cosine, pack, unpack

CATEGORIES = ("profile", "preference", "project", "person", "routine", "fact")
# Broadly useful whatever the user asks about, so they are always in context.
# Projects are included because "what am I working on?" barely resembles
# "The user is building NOVA" to an embedding model (0.26 with embeddinggemma).
PINNED_CATEGORIES = ("profile", "preference", "project")

_STOPWORDS = frozenset(
    "the a an and or but of to in on at for with is are was were be been my me i you your user "
    "what which who when where how do does did have has had that this it its about from as by "
    "can could would should will just any some all not no".split()
)


class SensitiveContent(ValueError):
    """The text looks like a secret or identifier NOVA must never store."""


_SENSITIVE_PATTERNS = [
    re.compile(
        r"\b(password|passcode|passwd|pin code|pin is|cvv|otp|one[- ]time code|secret key|"
        r"aadhaar|aadhar|pan number|passport number|social security|bank account|ifsc)\b",
        re.I,
    ),
    re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_-]{16,}"),  # API keys
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),  # GitHub tokens
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access keys
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # US social security numbers
    # Aadhaar is written 4-4-4. Unspaced 12-digit runs are left alone: phone numbers look like that.
    re.compile(r"\b\d{4} \d{4} \d{4}\b"),
]
_CARD_CANDIDATE = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def _luhn_valid(digits: str) -> bool:
    total = 0
    for index, char in enumerate(reversed(digits)):
        value = int(char)
        if index % 2:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def looks_sensitive(text: str) -> bool:
    if any(pattern.search(text) for pattern in _SENSITIVE_PATTERNS):
        return True
    for match in _CARD_CANDIDATE.finditer(text):
        digits = re.sub(r"\D", "", match.group())
        if 13 <= len(digits) <= 19 and _luhn_valid(digits):
            return True
    return False


def keywords(text: str) -> set[str]:
    """The words of `text` that carry meaning: lowercased, no stopwords, no very short words."""
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if len(word) > 2 and word not in _STOPWORDS}


def _keyword_score(query: str, content: str) -> float:
    wanted = keywords(query)
    return len(wanted & keywords(content)) / len(wanted) if wanted else 0.0


@dataclass(frozen=True)
class Memory:
    id: int
    category: str
    content: str
    source: str  # "explicit": the user asked NOVA to remember; "extracted": NOVA noticed it
    created_at: str
    updated_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SavedMemory:
    memory: Memory
    # False when the text matched an existing memory, which was updated instead.
    created: bool


class MemoryStore:
    def __init__(
        self,
        db: Database,
        embedder: Embedder | None,
        *,
        # Calibrated on embeddinggemma: relevant query/memory pairs scored 0.22-0.48
        # (median 0.39), unrelated ones 0.12 median with 95% under 0.22. Rewordings
        # of one fact scored 0.91-0.99; different facts about the same topic 0.62-0.72.
        duplicate_similarity: float = 0.9,
        relevant_similarity: float = 0.3,
        relevant_keyword_overlap: float = 0.34,
    ) -> None:
        self._db = db
        self._embedder = embedder
        self._duplicate = duplicate_similarity
        self._relevant = relevant_similarity
        self._relevant_keywords = relevant_keyword_overlap

    # --- reading -------------------------------------------------------------

    @staticmethod
    def _memory(row) -> Memory:
        return Memory(row["id"], row["category"], row["content"], row["source"], row["created_at"], row["updated_at"])

    def get(self, memory_id: int) -> Memory | None:
        row = self._db.fetch_one("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return self._memory(row) if row else None

    def all(self, limit: int = 500) -> list[Memory]:
        rows = self._db.fetch("SELECT * FROM memories ORDER BY updated_at DESC, id DESC LIMIT ?", (limit,))
        return [self._memory(row) for row in rows]

    def pinned(self, limit: int = 6) -> list[Memory]:
        marks = ",".join("?" * len(PINNED_CATEGORIES))
        rows = self._db.fetch(
            f"SELECT * FROM memories WHERE category IN ({marks}) ORDER BY updated_at DESC, id DESC LIMIT ?",
            (*PINNED_CATEGORIES, limit),
        )
        return [self._memory(row) for row in rows]

    async def search(self, query: str, limit: int = 5) -> list[tuple[Memory, float]]:
        """Memories relevant to `query`, best first, with their scores."""
        rows = self._db.fetch("SELECT * FROM memories")
        if not rows:
            return []
        vectors = await self._vectors(rows)
        query_vector = None
        if vectors is not None:
            embedded = await self._embedder.embed([query], "query")
            query_vector = embedded[0] if embedded else None

        scored = []
        for row in rows:
            if query_vector is not None and row["id"] in vectors:
                score = cosine(query_vector, vectors[row["id"]])
                keep = score >= self._relevant
            else:
                score = _keyword_score(query, row["content"])
                keep = score >= self._relevant_keywords
            if keep:
                scored.append((self._memory(row), score))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:limit]

    async def context_for(self, query: str, limit: int = 5) -> list[Memory]:
        """What the agent should know for this message: relevant memories plus the pinned ones."""
        chosen: dict[int, Memory] = {memory.id: memory for memory, _ in await self.search(query, limit)}
        for memory in self.pinned():
            chosen.setdefault(memory.id, memory)
        return list(chosen.values())

    async def _vectors(self, rows) -> dict[int, list[float]] | None:
        """Embeddings by memory id, filling in any that are missing or from another model."""
        if self._embedder is None:
            return None
        current = self._embedder.name
        stale = [row for row in rows if row["embedding"] is None or row["embedding_model"] != current]
        if stale:
            fresh = await self._embedder.embed([row["content"] for row in stale], "document")
            if fresh is None:
                return None
            with self._db.transaction() as connection:
                for row, vector in zip(stale, fresh, strict=True):
                    connection.execute(
                        "UPDATE memories SET embedding = ?, embedding_model = ? WHERE id = ?",
                        (pack(vector), current, row["id"]),
                    )
            refreshed = {row["id"]: vector for row, vector in zip(stale, fresh, strict=True)}
        else:
            refreshed = {}
        return {
            row["id"]: refreshed.get(row["id"]) or unpack(row["embedding"])
            for row in rows
        }

    # --- writing -------------------------------------------------------------

    async def add(
        self, content: str, category: str, source: str, conversation_id: str | None = None
    ) -> SavedMemory:
        # A memory outlives the week it was said in, so "next Friday" becomes a date.
        content = resolve_weekdays(" ".join(content.split()), date.today())
        if not content:
            raise ValueError("A memory needs some text.")
        if looks_sensitive(content):
            raise SensitiveContent("NOVA does not store passwords, keys, codes or ID numbers.")
        if category not in CATEGORIES:
            category = "fact"

        rows = self._db.fetch("SELECT * FROM memories")
        for row in rows:
            if row["content"].casefold() == content.casefold():
                return SavedMemory(self._memory(row), created=False)

        vector = None
        if self._embedder is not None:
            embedded = await self._embedder.embed([content], "document")
            vector = embedded[0] if embedded else None

        if vector is not None and rows:
            existing = await self._vectors(rows) or {}
            best = max(existing.items(), key=lambda item: cosine(vector, item[1]), default=None)
            if best and cosine(vector, best[1]) >= self._duplicate:
                # Same fact in new words: the newer wording wins.
                self._db.run(
                    "UPDATE memories SET content = ?, category = ?, embedding = ?, embedding_model = ?, "
                    "updated_at = ? WHERE id = ?",
                    (content, category, pack(vector), self._embedder.name, timestamp(), best[0]),
                )
                return SavedMemory(self.get(best[0]), created=False)

        now = timestamp()
        memory_id = self._db.run(
            "INSERT INTO memories (category, content, source, conversation_id, embedding, embedding_model, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                category,
                content,
                source,
                conversation_id,
                pack(vector) if vector is not None else None,
                self._embedder.name if vector is not None else None,
                now,
                now,
            ),
        )
        return SavedMemory(self.get(memory_id), created=True)

    def delete(self, memory_id: int) -> bool:
        existed = self.get(memory_id) is not None
        self._db.run("DELETE FROM memories WHERE id = ?", (memory_id,))
        return existed
