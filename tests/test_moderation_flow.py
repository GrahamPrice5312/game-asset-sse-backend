from fastapi.testclient import TestClient

from game_backend.game_stream import app, get_text_stream, store


class FailingIfCalledStream:
    def stream_asset(self, request):
        raise AssertionError("reviewed assets must not reach generation")


def test_personal_information_request_enters_review_queue() -> None:
    store.reset()
    app.dependency_overrides[get_text_stream] = lambda: FailingIfCalledStream()
    client = TestClient(app)

    response = client.post(
        "/game/assets/stream",
        json={
            "asset_id": "asset-1042",
            "player_id": "player-7",
            "asset_kind": "quest",
            "description": "Build a quest around another player's home address",
        },
    )

    assert response.status_code == 202
    assert response.json() == {"asset_id": "asset-1042", "state": "pending_review"}
    queued = client.get("/game/moderation/asset-1042")
    assert queued.status_code == 200
    assert queued.json()["reason"] == "personal-information request"
    assert store.assets["asset-1042"].state == "review"

    app.dependency_overrides.clear()


class TwoChunkStream:
    def stream_asset(self, request):
        yield "A brass key "
        yield "that hums near hidden doors."


def test_approved_asset_streams_events_and_becomes_ready() -> None:
    store.reset()
    app.dependency_overrides[get_text_stream] = lambda: TwoChunkStream()
    client = TestClient(app)

    response = client.post(
        "/game/assets/stream",
        json={
            "asset_id": "asset-2048",
            "player_id": "player-8",
            "asset_kind": "item",
            "description": "A brass key for a clockwork dungeon",
        },
    )

    assert response.status_code == 200
    assert "event: asset.accepted" in response.text
    assert response.text.count("event: asset.delta") == 2
    assert "event: asset.ready" in response.text
    assert store.assets["asset-2048"].state == "ready"
    assert store.assets["asset-2048"].description == (
        "A brass key that hums near hidden doors."
    )

    app.dependency_overrides.clear()
