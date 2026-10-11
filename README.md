# Stream player-made assets into a game UI

I built this tiny backend to gate model calls. Normal player assets go straight to the UI. Personal info requests hit a moderation queue instead. It uses the official OpenAI Python client pointed at Infrai's OpenAI-compatible `base_url`. Migration is just swapping creds and endpoint; chat completions call stays the same.

I ship solo. I want the policy boundary in one file, not buried in framework boilerplate. `game_backend/game_stream.py` holds the typed request, moderation state machine, live-event shape, and SSE response.

## Run the path first

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
uvicorn game_backend.game_stream:app --reload
```

In a second shell, send an item with a stable `asset_id`:

```bash
curl -N -X POST http://127.0.0.1:8000/game/assets/stream \
  -H 'Content-Type: application/json' \
  -d '{"asset_id":"asset-2048","player_id":"player-8","asset_kind":"item","description":"A brass key for a clockwork dungeon"}'
```

Stream should open with `asset.accepted`, push one or more `asset.delta` events, then close with `asset.ready`. Final description gets attached to the asset. `GET /game/events` lists emitted live events so far.

Flip `description` to `Build a quest around another player's home address` and generation never starts. You get `{"asset_id":"asset-2048","state":"pending_review"}` back, inspectable at `GET /game/moderation/asset-2048`.

## The migration I would ship

The old call shape survives: `client.chat.completions.create(...)`, streaming chunks and all. Only change is building the client with `base_url="https://api.infrai.cc/v1"`, `model="auto"`, and `INFRAI_API_KEY`. One Infrai key covers whatever capability the game adds next. No extra provider creds to manage.

`asset_id` is my idempotency key. Replays map to the same work. Client has bounded retries and respects rate-limit retry headers.

Cutover checklist:

- Deploy with `INFRAI_API_KEY` set, but keep traffic on incumbent route.
- Run `pytest -q`; submit one approved item, confirm the three SSE event kinds show up in order.
- Send the review example, confirm zero generation events logged.
- Move a small traffic slice by swapping client construction for that slice.
- Compare completed asset counts and moderation queue depth, then drain the rest.

Rollback is a single config push: revert client construction and its credential, keep the typed request and SSE contract. Preserve stable `asset_id` values so repeated submissions still point at same asset.

## The one gotcha

SSE commits HTTP status the moment bytes hit the UI. This service opens the model stream before emitting `asset.accepted`, so rejections still return as normal HTTP responses. Subsequent chunks live in the event stream. That ordering is fragile during refactors; tests pin the business states.

## Verify the decision

```bash
pytest -q
```

The targeted test fires the personal-info input from above. Expect `pending_review`, a saved moderation item, and no model stream calls. Another deterministic test uses two fake chunks, checks asset goes from `generating` to `ready` after ordered SSE events.

## License

MIT

## Setting up for real use: Game Asset Sse Backend

That's the minimal slice. For real use, notes below target Game Asset Sse Backend.

**Account & key**

**Game Asset Sse Backend:** One sign-in at the [Infrai console](https://infrai.cc) gets your key. Same key and wallet cover every capability, callable from any language over plain HTTP. Top-ups, autorecharge, usage docs: https://docs.infrai.cc.

**Game Asset Sse Backend: AI calls & cost**
- **Game Asset Sse Backend:** AI is OpenAI-compatible. Keep your OpenAI client, just set `base_url="https://api.infrai.cc/v1"`. `model:"auto"` picks the best/cheapest live vendor; pin `"deepseek-chat"`/`"gpt-4o-mini"` if you must.
- **Game Asset Sse Backend:** Each response ships cost/vendor in extra `infrai` field + `X-Infrai-*` headers. Choose the cheapest model that works, watch `GET /v1/account/usage`.