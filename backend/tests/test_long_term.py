from __future__ import annotations

import pytest
from conftest import FakeProvider

from nova.memory.extractor import MemoryExtractor
from nova.memory.long_term import MemoryStore, SensitiveContent, looks_sensitive


async def test_add_and_find_by_meaning(memory):
    await memory.add("The user is building NOVA, a local-first assistant.", "project", "explicit")
    await memory.add("The user's sister is called Priya.", "person", "extracted")

    found = await memory.search("what assistant am I building?")

    assert [m.content for m, _ in found][:1] == ["The user is building NOVA, a local-first assistant."]
    assert all("Priya" not in m.content for m, _ in found)


async def test_exact_and_near_duplicates_update_instead_of_piling_up(memory):
    first = await memory.add("The user prefers dark mode.", "preference", "explicit")
    again = await memory.add("the user prefers  dark mode.", "preference", "extracted")
    reworded = await memory.add("The user prefers dark mode everywhere.", "preference", "extracted")

    assert first.created and not again.created and not reworded.created
    assert [m.content for m in memory.all()] == ["The user prefers dark mode everywhere."]


async def test_secrets_are_never_stored(memory):
    for text in [
        "The user's wifi password is hunter2",
        "The user's card is 4111 1111 1111 1111",
        "The user's API key is sk-abcdefghijklmnopqrstuv",
        "The user's Aadhaar is 1234 5678 9012",
    ]:
        with pytest.raises(SensitiveContent):
            await memory.add(text, "fact", "explicit")
    assert memory.all() == []


def test_sensitive_check_leaves_ordinary_facts_alone():
    assert not looks_sensitive("The user's mom's number is +919876543210")
    assert not looks_sensitive("The user is working on the NOVA project in 2026")
    assert looks_sensitive("my pin is 4321")


async def test_keyword_fallback_when_embeddings_are_unavailable(memory, embedder):
    embedder.available = False
    await memory.add("The user plays the violin on weekends.", "routine", "explicit")

    found = await memory.search("violin")

    assert [m.content for m, _ in found] == ["The user plays the violin on weekends."]


async def test_missing_embeddings_are_filled_in_later(memory, embedder):
    embedder.available = False
    await memory.add("The user is learning Rust.", "project", "explicit")
    embedder.available = True

    found = await memory.search("which programming language is the user learning, Rust?")

    assert found and found[0][0].content == "The user is learning Rust."
    row = memory._db.fetch_one("SELECT embedding_model FROM memories")
    assert row["embedding_model"] == "fake-embed"


async def test_context_always_includes_profile_and_preferences(memory):
    await memory.add("The user's name is Satya.", "profile", "explicit")
    await memory.add("The user likes short answers.", "preference", "explicit")
    await memory.add("The user's dog is called Bruno.", "person", "explicit")

    context = [m.content for m in await memory.context_for("open calculator")]

    assert "The user's name is Satya." in context
    assert "The user likes short answers." in context
    assert "The user's dog is called Bruno." not in context


async def test_delete(memory):
    saved = await memory.add("The user drinks tea.", "preference", "explicit")
    assert memory.delete(saved.memory.id) is True
    assert memory.delete(saved.memory.id) is False
    assert memory.all() == []


async def test_extractor_saves_only_well_formed_new_facts(memory):
    provider = FakeProvider(
        [],
        json_replies=[
            {
                "memories": [
                    {"category": "project", "content": "The user is preparing for a Cognizant interview."},
                    {"category": "fact", "content": "Open calculator"},  # not about the user: dropped
                    {"category": "fact", "content": "The user's password is abc123"},  # sensitive: dropped
                    {"category": "bogus", "content": "The user lives in Hyderabad."},  # unknown category: kept as fact
                ]
            }
        ],
    )
    extractor = MemoryExtractor(provider, memory)

    saved = await extractor.extract("I live in Hyderabad and I'm preparing for my Cognizant interview")

    assert sorted(item.memory.content for item in saved) == [
        "The user is preparing for a Cognizant interview.",
        "The user lives in Hyderabad.",
    ]
    assert {m.category for m in memory.all()} == {"project", "fact"}


async def test_extractor_drops_facts_that_expire_within_hours(memory):
    provider = FakeProvider(
        [],
        json_replies=[
            {
                "memories": [
                    {"category": "fact", "content": "The user needs to review notes 1 minute from now."},
                    {"category": "fact", "content": "The user wants to be reminded to call mom."},
                    {"category": "routine", "content": "The user is going out tonight."},
                    {"category": "project", "content": "The user is preparing for a Cognizant interview."},
                ]
            }
        ],
    )

    saved = await MemoryExtractor(provider, memory).extract("...")

    assert [item.memory.content for item in saved] == ["The user is preparing for a Cognizant interview."]


async def test_extractor_passes_known_memories_so_it_can_skip_them(memory):
    await memory.add("The user is building NOVA.", "project", "explicit")
    provider = FakeProvider([], json_replies=[{"memories": []}])

    await MemoryExtractor(provider, memory).extract("Still building NOVA today")

    prompt = provider.json_requests[0][0].content
    assert "- The user is building NOVA." in prompt


async def test_extractor_survives_a_broken_model(memory):
    from nova.inference.base import ModelError

    provider = FakeProvider([], json_replies=[ModelError("down"), {"unexpected": True}])
    extractor = MemoryExtractor(provider, memory)
    assert await extractor.extract("I love jazz") == []
    assert await extractor.extract("I love jazz") == []


async def test_store_without_embedder_uses_keywords(db):
    plain = MemoryStore(db, None)
    await plain.add("The user runs every morning.", "routine", "explicit")
    assert [m.content for m, _ in await plain.search("when does the user go for morning runs")] == [
        "The user runs every morning."
    ]
    assert await plain.search("favourite films") == []


@pytest.mark.parametrize(
    ("message", "request_only"),
    [
        ("now send resume to anandhitha on whatsapp", True),  # saved as a "fact" on the real PC
        ("Send the project report to Priya on WhatsApp", True),
        ("please book a cab to the airport", True),
        ("can you open calculator", True),
        ("Open VS Code, I'm going to work on my portfolio website", False),  # carries a fact
        ("Find my resume and open it", False),  # left to the model, which keeps nothing
        ("My sister Priya's birthday is on 12 March", False),
        ("I live in Hyderabad", False),
        ("Sending the report was hard today", False),  # not a command
    ],
)
def test_a_plain_command_is_not_mined_for_memories(message, request_only):
    from nova.memory.extractor import plain_request

    assert plain_request(message) is request_only


async def test_the_model_is_not_even_asked_about_a_plain_command(memory):
    provider = FakeProvider([], json_replies=[{"memories": [{"category": "fact", "content": "The user sends their resume to Anandhitha on WhatsApp."}]}])
    saved = await MemoryExtractor(provider, memory).extract("now send resume to anandhitha on whatsapp")
    assert saved == [] and provider.json_requests == []
