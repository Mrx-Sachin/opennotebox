"""Tests for flashcard endpoints, authoring, hinting, queuing, and resetting."""
from __future__ import annotations

from httpx import AsyncClient


class TestFlashcardHintAndAuthoring:
    async def test_create_basic_card_with_hint(
        self, client: AsyncClient, auth_header: dict, test_document
    ):
        """Test creating a basic flashcard with a hint."""
        payload = {
            "document_id": test_document.id,
            "card_type": "basic",
            "question": "What is the capital of France?",
            "answer": "Paris",
            "hint": "City of Light",
        }
        resp = await client.post("/api/flashcards", headers=auth_header, json=payload)
        assert resp.status_code == 200
        cards = resp.json()
        assert len(cards) == 1
        card = cards[0]
        assert card["card_type"] == "basic"
        assert card["question"] == "What is the capital of France?"
        assert card["answer"] == "Paris"
        assert card["hint"] == "City of Light"

        # Check that GET /api/flashcards also includes the hint
        list_resp = await client.get(
            f"/api/flashcards?document_id={test_document.id}",
            headers=auth_header,
        )
        assert list_resp.status_code == 200
        listed = [c for c in list_resp.json() if c["id"] == card["id"]]
        assert len(listed) == 1
        assert listed[0]["hint"] == "City of Light"

    async def test_create_reversed_card_with_hint(
        self, client: AsyncClient, auth_header: dict, test_document
    ):
        """Test creating reversed flashcards with a hint."""
        payload = {
            "document_id": test_document.id,
            "card_type": "reversed",
            "question": "Dog",
            "answer": "Chien",
            "hint": "Canine",
        }
        resp = await client.post("/api/flashcards", headers=auth_header, json=payload)
        assert resp.status_code == 200
        cards = resp.json()
        assert len(cards) == 2
        for card in cards:
            assert card["card_type"] == "basic"
            assert card["hint"] == "Canine"
            assert card["group_id"] == cards[0]["id"]

    async def test_create_cloze_card_with_hint_markup(
        self, client: AsyncClient, auth_header: dict, test_document
    ):
        """Test creating a cloze card with {{c1::answer::hint}} markup."""
        payload = {
            "document_id": test_document.id,
            "card_type": "cloze",
            "cloze_text": "The {{c1::mitochondria::powerhouse}} is the powerhouse of the cell.",
        }
        resp = await client.post("/api/flashcards", headers=auth_header, json=payload)
        assert resp.status_code == 200
        cards = resp.json()
        assert len(cards) == 1
        card = cards[0]
        assert card["card_type"] == "cloze"
        assert card["cloze_index"] == 1
        assert card["hint"] == "powerhouse"
        assert "[powerhouse]" in card["question"]

    async def test_create_multi_cloze_card_with_hints(
        self, client: AsyncClient, auth_header: dict, test_document
    ):
        """Test creating multi-span cloze card where each span has its own hint."""
        cloze_text = (
            "{{c1::Python::language}} was created by {{c2::Guido van Rossum::creator}} in 1991."
        )
        payload = {
            "document_id": test_document.id,
            "card_type": "cloze",
            "cloze_text": cloze_text,
        }
        resp = await client.post("/api/flashcards", headers=auth_header, json=payload)
        assert resp.status_code == 200
        cards = resp.json()
        assert len(cards) == 2

        card1 = next(c for c in cards if c["cloze_index"] == 1)
        card2 = next(c for c in cards if c["cloze_index"] == 2)
        assert card1["hint"] == "language"
        assert card2["hint"] == "creator"

    async def test_update_flashcard_hint(
        self, client: AsyncClient, auth_header: dict, test_document
    ):
        """Test updating question, answer, and hint on an existing card."""
        create_resp = await client.post(
            "/api/flashcards",
            headers=auth_header,
            json={
                "document_id": test_document.id,
                "card_type": "basic",
                "question": "Q1",
                "answer": "A1",
                "hint": "Old hint",
            },
        )
        card_id = create_resp.json()[0]["id"]

        update_resp = await client.put(
            f"/api/flashcards/{card_id}",
            headers=auth_header,
            json={"hint": "New updated hint"},
        )
        assert update_resp.status_code == 200
        assert update_resp.json()["hint"] == "New updated hint"


class TestFlashcardQueueModes:
    async def test_queue_mode_due_vs_all(
        self, client: AsyncClient, auth_header: dict, test_document
    ):
        """Test queue retrieval with mode=due vs mode=all."""
        # Create a card
        create_resp = await client.post(
            "/api/flashcards",
            headers=auth_header,
            json={
                "document_id": test_document.id,
                "card_type": "basic",
                "question": "What is 2+2?",
                "answer": "4",
                "hint": "Basic math",
            },
        )
        card = create_resp.json()[0]
        card_id = card["id"]

        # Initial queue with mode=due: should contain card (reps=0 is new card)
        due_resp = await client.get(
            f"/api/flashcards/queue?document_id={test_document.id}&mode=due",
            headers=auth_header,
        )
        assert due_resp.status_code == 200
        queue_items = due_resp.json()
        assert any(item["id"] == card_id for item in queue_items)
        target_item = next(item for item in queue_items if item["id"] == card_id)
        assert target_item["hint"] == "Basic math"
        assert "preview_intervals" in target_item

        # Initial queue with mode=all: should also contain card
        all_resp = await client.get(
            f"/api/flashcards/queue?document_id={test_document.id}&mode=all",
            headers=auth_header,
        )
        assert all_resp.status_code == 200
        all_items = all_resp.json()
        assert any(item["id"] == card_id for item in all_items)
        all_target = next(item for item in all_items if item["id"] == card_id)
        assert all_target["hint"] == "Basic math"
        assert "preview_intervals" in all_target

        # Review the card with Good (3) or Easy (4) to schedule it in the future
        review_resp = await client.post(
            f"/api/flashcards/{card_id}/review",
            headers=auth_header,
            json={"rating": 3},
        )
        assert review_resp.status_code == 200
        reviewed = review_resp.json()
        assert reviewed["reps"] == 1
        assert reviewed["hint"] == "Basic math"

        # Now mode=due should NOT contain this card because it is not due yet (due > now and reps > 0)
        due_resp2 = await client.get(
            f"/api/flashcards/queue?document_id={test_document.id}&mode=due",
            headers=auth_header,
        )
        assert due_resp2.status_code == 200
        due_ids = [item["id"] for item in due_resp2.json()]
        assert card_id not in due_ids

        # But mode=all SHOULD still contain this card!
        all_resp2 = await client.get(
            f"/api/flashcards/queue?document_id={test_document.id}&mode=all",
            headers=auth_header,
        )
        assert all_resp2.status_code == 200
        all_ids = [item["id"] for item in all_resp2.json()]
        assert card_id in all_ids


class TestFlashcardReset:
    async def test_reset_single_flashcard(
        self, client: AsyncClient, auth_header: dict, test_document
    ):
        """Test POST /api/flashcards/{id}/reset restores a card to new/due status."""
        create_resp = await client.post(
            "/api/flashcards",
            headers=auth_header,
            json={
                "document_id": test_document.id,
                "card_type": "basic",
                "question": "What is the capital of Japan?",
                "answer": "Tokyo",
                "hint": "East Asia",
            },
        )
        card_id = create_resp.json()[0]["id"]

        # Review it so reps > 0
        await client.post(
            f"/api/flashcards/{card_id}/review",
            headers=auth_header,
            json={"rating": 4},
        )

        # Confirm it is not in due queue
        due_resp = await client.get(
            f"/api/flashcards/queue?document_id={test_document.id}&mode=due",
            headers=auth_header,
        )
        assert card_id not in [item["id"] for item in due_resp.json()]

        # Reset single card
        reset_resp = await client.post(
            f"/api/flashcards/{card_id}/reset",
            headers=auth_header,
        )
        assert reset_resp.status_code == 200
        data = reset_resp.json()
        assert data["id"] == card_id
        assert data["reps"] == 0
        assert data["lapses"] == 0
        assert data["fsrs_state"] == 1
        assert data["stability"] is None
        assert data["difficulty"] is None
        assert data["last_review"] is None
        assert data["hint"] == "East Asia"

        # Now it must be back in mode=due queue
        due_resp2 = await client.get(
            f"/api/flashcards/queue?document_id={test_document.id}&mode=due",
            headers=auth_header,
        )
        assert card_id in [item["id"] for item in due_resp2.json()]

    async def test_reset_deck(
        self, client: AsyncClient, auth_header: dict, test_document
    ):
        """Test POST /api/flashcards/reset resets all cards in the document."""
        # Create 2 cards
        c1 = (
            await client.post(
                "/api/flashcards",
                headers=auth_header,
                json={
                    "document_id": test_document.id,
                    "card_type": "basic",
                    "question": "Card A",
                    "answer": "Ans A",
                },
            )
        ).json()[0]

        c2 = (
            await client.post(
                "/api/flashcards",
                headers=auth_header,
                json={
                    "document_id": test_document.id,
                    "card_type": "basic",
                    "question": "Card B",
                    "answer": "Ans B",
                },
            )
        ).json()[0]

        # Review both cards
        await client.post(
            f"/api/flashcards/{c1['id']}/review",
            headers=auth_header,
            json={"rating": 3},
        )
        await client.post(
            f"/api/flashcards/{c2['id']}/review",
            headers=auth_header,
            json={"rating": 4},
        )

        # Neither should be in due queue
        due_resp = await client.get(
            f"/api/flashcards/queue?document_id={test_document.id}&mode=due",
            headers=auth_header,
        )
        due_ids = [item["id"] for item in due_resp.json()]
        assert c1["id"] not in due_ids
        assert c2["id"] not in due_ids

        # Reset deck
        reset_resp = await client.post(
            f"/api/flashcards/reset?document_id={test_document.id}",
            headers=auth_header,
        )
        assert reset_resp.status_code == 200
        res_data = reset_resp.json()
        assert res_data["message"] == "Deck reset successfully"
        assert res_data["count"] >= 2

        # Both should now be in due queue
        due_resp2 = await client.get(
            f"/api/flashcards/queue?document_id={test_document.id}&mode=due",
            headers=auth_header,
        )
        due_ids2 = [item["id"] for item in due_resp2.json()]
        assert c1["id"] in due_ids2
        assert c2["id"] in due_ids2
