# Spike 001: narrow Codex-subscription adapter

Question: can the Codex text-request path be made small enough to use without installing Hermes Agent or vendoring its broad auxiliary-client dependency closure?

## Current design

`codex_adapter.py` implements only the path this project needs:

1. Read the existing `openai-codex` OAuth grant from `$HERMES_HOME/auth.json` (default `~/.hermes/auth.json`).
2. When the JWT is close to expiry, refresh it and atomically update the same auth file.
3. Send a text-only request to the Codex Responses endpoint using the OpenAI SDK. The endpoint requires `stream=true` and typed input items.
4. Return text deltas only after `response.completed`; failed or truncated streams are rejected.

The adapter identifies itself as `commahum` and forwards the ChatGPT account and residency headers encoded in the access JWT. It uses the same adjacent `.lock` naming and POSIX `flock` convention as Hermes when refreshing rotating credentials.

The implementation is deliberately a narrow port/adaptation, not a runtime import of Hermes and not a wholesale copy of its files. `manifest.json` pins the upstream revision and identifies the source modules/functions used as references. The upstream MIT notice is retained in `LICENSE.hermes`.

## Verification

The Python unit tests use fake Codex Responses events and fake OAuth responses, so they make no network calls. They cover typed request construction, identity headers, refusing incomplete/failed output, token refresh rotation, preserving unrelated auth-store data, missing credentials, HTTP 401/429 handling, ambiguous transport/status failures, malformed refresh responses, write failure after rotation, and concurrent refresh callers across threads and processes. Before POSTing, the adapter atomically persists a one-way fingerprint of the current refresh token; it refuses to replay that token after an uncertain response or a failed auth-store write. An accepted new token pair replaces the marker.

With a clean virtual environment, install `requirements.txt`, then run:

```sh
python -m unittest -v test_codex_adapter
python smoke.py missing-auth
python smoke.py success
```

`success` sends one small live request and needs an existing Hermes Codex OAuth login. The narrow adapter has also been exercised from a venv where the `hermes-agent` distribution is absent.

## Observed result

The first working probe dynamically copied 117 Python modules (about 3.3 MB) just to call one text request through `agent.auxiliary_client`. That snapshot is no longer needed: the current direct adapter is about 11 KB and depends at runtime only on the OpenAI SDK, `httpx`, and the existing auth file. A direct non-streaming call was rejected by Codex; a typed `stream=true` call completed successfully.

## Verdict: PARTIAL

The slim request path and a real subscription request work without the Hermes package. Unit tests verify atomic token rotation, including a race between separate processes; explicit 401/429 responses leave stored credentials unchanged. Before any refresh POST, the adapter persists only a hash of the current refresh token. Uncertain transport failures, unexpected HTTP statuses, malformed 200 responses, or a failed auth-store write keep that guard in place, preventing this adapter from replaying the token. A new grant can resume operation; other clients that ignore this adapter-specific marker could still try the old token, so re-authenticate with Hermes before using another client. Initial sign-in remains outside this adapter. It currently targets POSIX systems because it shares the auth store's `flock` lock.

Next: move the small adapter from the spike folder into the project package when the project layout is established.
