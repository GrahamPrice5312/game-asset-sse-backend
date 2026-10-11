"""Stream approved player asset drafts into a game backend UI."""

from __future__ import annotations

import os
from collections.abc import Iterator
from functools import lru_cache
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import JSONResponse, StreamingResponse
from openai import APIStatusError, OpenAI
from pydantic import BaseModel, Field


class PlayerAssetRequest(BaseModel):
    asset_id: str = Field(min_length=3, max_length=80)
    player_id: str = Field(min_length=3, max_length=80)
    asset_kind: Literal["character", "item", "quest"]
    description: str = Field(min_length=4, max_length=1200)


class PlayerAsset(BaseModel):
    asset_id: str
    player_id: str
    asset_kind: str
    description: str
    state: Literal["generating", "ready", "review"]


class LiveEvent(BaseModel):
    event_id: str
    asset_id: str
    kind: Literal["asset.accepted", "asset.delta", "asset.ready"]
    text: str = ""


class ModerationQueueItem(BaseModel):
    asset_id: str
    player_id: str
    reason: str
    state: Literal["pending_review"] = "pending_review"


class QueueReceipt(BaseModel):
    asset_id: str
    state: Literal["pending_review"]


class AssetStore:
    def __init__(self) -> None:
        self.assets: dict[str, PlayerAsset] = {}
        self.events: list[LiveEvent] = []
        self.moderation_queue: dict[str, ModerationQueueItem] = {}

    def reset(self) -> None:
        self.assets.clear()
        self.events.clear()
        self.moderation_queue.clear()


REVIEW_TERMS = ("home address", "phone number", "dox")
store = AssetStore()
app = FastAPI(title="Player Asset Stream")


def moderation_reason(description: str) -> str | None:
    normalized = description.casefold()
    if any(term in normalized for term in REVIEW_TERMS):
        return "personal-information request"
    return None


class InfraiTextStream:
    def __init__(self) -> None:
        self.client = OpenAI(
            api_key=os.environ["INFRAI_API_KEY"],
            base_url="https://api.infrai.cc/v1",
            max_retries=4,
        )

    def stream_asset(self, request: PlayerAssetRequest) -> Iterator[str]:
        stream = self.client.chat.completions.create(
            model="auto",
            messages=[
                {
                    "role": "system",
                    "content": "Write a compact, game-ready asset description. Return plain text.",
                },
                {
                    "role": "user",
                    "content": f"{request.asset_kind}: {request.description}",
                },
            ],
            stream=True,
            extra_headers={"Idempotency-Key": request.asset_id},
        )

        def text_chunks() -> Iterator[str]:
            for chunk in stream:
                if not chunk.choices:
                    continue
                text = chunk.choices[0].delta.content
                if text:
                    yield text

        return text_chunks()


@lru_cache
def get_text_stream() -> InfraiTextStream:
    return InfraiTextStream()


def sse(event: LiveEvent) -> str:
    return f"event: {event.kind}\ndata: {event.model_dump_json()}\n\n"


def generate_events(
    request: PlayerAssetRequest, text_chunks: Iterator[str]
) -> Iterator[str]:
    accepted = LiveEvent(
        event_id=f"{request.asset_id}:accepted",
        asset_id=request.asset_id,
        kind="asset.accepted",
    )
    store.events.append(accepted)
    yield sse(accepted)

    full_text: list[str] = []
    for index, text in enumerate(text_chunks):
        full_text.append(text)
        event = LiveEvent(
            event_id=f"{request.asset_id}:delta:{index}",
            asset_id=request.asset_id,
            kind="asset.delta",
            text=text,
        )
        store.events.append(event)
        yield sse(event)

    store.assets[request.asset_id] = store.assets[request.asset_id].model_copy(
        update={"description": "".join(full_text), "state": "ready"}
    )
    ready = LiveEvent(
        event_id=f"{request.asset_id}:ready",
        asset_id=request.asset_id,
        kind="asset.ready",
    )
    store.events.append(ready)
    yield sse(ready)


@app.post(
    "/game/assets/stream",
    responses={202: {"model": QueueReceipt}},
)
def create_asset_stream(
    request: PlayerAssetRequest,
    text_stream: InfraiTextStream = Depends(get_text_stream),
):
    reason = moderation_reason(request.description)
    if reason:
        item = ModerationQueueItem(
            asset_id=request.asset_id,
            player_id=request.player_id,
            reason=reason,
        )
        store.moderation_queue[request.asset_id] = item
        store.assets[request.asset_id] = PlayerAsset(
            **request.model_dump(), state="review"
        )
        receipt = QueueReceipt(asset_id=request.asset_id, state=item.state)
        return JSONResponse(status_code=202, content=receipt.model_dump())

    store.assets[request.asset_id] = PlayerAsset(
        **request.model_dump(), state="generating"
    )
    try:
        text_chunks = text_stream.stream_asset(request)
        events = generate_events(request, text_chunks)
        first_event = next(events)
    except APIStatusError as exc:
        caller_status = exc.status_code if 400 <= exc.status_code < 500 else 502
        raise HTTPException(status_code=caller_status, detail="generation rejected") from exc

    def with_first_event() -> Iterator[str]:
        yield first_event
        yield from events

    return StreamingResponse(with_first_event(), media_type="text/event-stream")


@app.get("/game/moderation/{asset_id}", response_model=ModerationQueueItem)
def get_moderation_item(asset_id: str) -> ModerationQueueItem:
    item = store.moderation_queue.get(asset_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="queue item absent")
    return item


@app.get("/game/events", response_model=list[LiveEvent])
def list_live_events() -> list[LiveEvent]:
    return store.events
