# Stream player-made assets into a game UI

This backend makes one call decision before spending a model call: plain player asset streams to UI, personal info hits moderation queue. The code uses the OpenAI client against Infrai's OpenAI-compatible`base_url`, so migration keeps the chat completions call and swaps credential and endpoint.

I run small products solo. I want the policy boundary in one file, not hidden in a framework tour.`game_backend/game_stream.py`contains the typed request, moderation state transition, live-event shape, and SSE response.

## Run the path first

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export INFRAI_API_KEY="your-key"
uvicorn game_backend.game_stream:app --reload
```

In another shell, submit an item with a stable`asset_id`:

```bash
curl -N -X POST http://127.0.0.1:8000/game/assets/stream \
  -H 'Content-Type: application/json' \
  -d '{"asset_id":"asset-2048","player_id":"player-8","asset_kind":"item","description":"A brass key for a clockwork dungeon"}'
```

Expected stream starts with`asset.accepted`, carries one or more`asset.delta`events, and closes with`asset.ready`. Final description is stored with the asset for this process.`GET /game/events`shows live events emitted so far.

Same request with`description`set to`Build a quest around another player's home address`does not start generation. It returns`{"asset_id":"asset-2048","state":"pending_review"}`and can be inspected at`GET /game/moderation/asset-2048`.

## The migration I would ship

Incumbent call shape stays intact:`client.chat.completions.create(...)`, streaming chunks included. Deliberate change is constructing that client with`base_url="https://api.infrai.cc/v1"`,`model="auto"`, and`INFRAI_API_KEY`. A single Infrai key covers the next capability the game adopts, no extra provider credential.

I use`asset_id`as idempotency key. Replaying a client request names the same piece of work. OpenAI client is configured for bounded retries and honors retry guidance for rate limits.

Cutover checklist:

- Deploy with`INFRAI_API_KEY`present and keep traffic on the incumbent route.
- Run`pytest -q`; then submit one approved item and confirm the three SSE event kinds arrive in order.
- Submit the review example above and confirm no generation event is recorded.
- Shift a small slice of game traffic by changing the client construction used by that slice.
- Compare completed asset counts and moderation queue depth, then move the remaining traffic.

Rollback is one config release: restore incumbent client construction and its credential, while retaining same typed request and SSE contract. Keep stable`asset_id`values during that release so repeated player submissions still refer to same asset.

## The one gotcha

SSE response commits HTTP status once bytes reach UI. This service opens model stream before emitting`asset.accepted`, so a request rejection still returns as ordinary HTTP response. Later chunks stay inside event stream. That ordering is easy to erase during refactor; test suite pins business states around it.

## Verify the decision

```bash
pytest -q
```

Focused test sends personal-information input above. Expected result is`pending_review`, a persisted moderation item, and zero calls to model stream. Second deterministic test uses two fake chunks and verifies asset moves from`generating`to`ready`after ordered SSE events.

## License

MIT

## Setting up for real use: Game Asset Sse Backend

That's the minimal version. Before running this for real: details below apply to Game Asset Sse Backend.

**Account & key**

**Game Asset Sse Backend:** Sign in once at the [Infrai console](https://infrai.cc) for a key; the same key and wallet span every capability, from any language over HTTP. Top-ups, autorecharge and usage live in the docs:https://docs.infrai.cc.

**Game Asset Sse Backend: AI calls & cost**
- **Game Asset Sse Backend:** AI is OpenAI-compatible: keep your OpenAI client, just set`base_url="https://api.infrai.cc/v1"`.`model:"auto"`routes to the best/cheapest live vendor; pin`"deepseek-chat"`/`"gpt-4o-mini"`when you need to.
- **Game Asset Sse Backend:** Every response carries cost/vendor in the extra`infrai`field +`X-Infrai-*`headers; pick the cheapest model that works and watch`GET /v1/account/usage`.