# Spike 001: narrow Codex-subscription adapter

Question: can the Codex text-request path be made small enough to use without installing Hermes Agent or vendoring its broad auxiliary-client dependency closure?

**Graduated.** The adapter this spike produced now lives in the project package as
`src/commahum/codex_adapter.py`, with its tests in `tests/test_codex_adapter.py`
and its live smoke script in `examples/smoke_codex.py`. This folder is kept as the
provenance record: `manifest.json` pins the upstream revision and names the source
modules used as references, and `LICENSE.hermes` carries the upstream MIT notice
for the derived implementation. `requirements.txt` records the pins that the
`codex` extra of `pyproject.toml` now declares.

Question answered: yes. Verdict: PARTIAL — see below for the limits that remain.

## What the adapter implements

Only the path this project needs:

1. Read the existing `openai-codex` OAuth grant from `$HERMES_HOME/auth.json` (default `~/.hermes/auth.json`).
2. When the JWT is close to expiry, refresh it and atomically update the same auth file.
3. Send a text-only request to the Codex Responses endpoint using the OpenAI SDK. The endpoint requires `stream=true` and typed input items.
4. Return text deltas only after `response.completed`; failed or truncated streams are rejected.

The adapter identifies itself as `commahum` and forwards the ChatGPT account and residency headers encoded in the access JWT. It uses the same adjacent `.lock` naming and POSIX `flock` convention as Hermes when refreshing rotating credentials.

The implementation is deliberately a narrow port/adaptation, not a runtime import of Hermes and not a wholesale copy of its files.

## Verification

The Python unit tests use fake Codex Responses events and fake OAuth responses, so they make no network calls. They cover typed request construction, identity headers, refusing incomplete/failed output, token refresh rotation, preserving unrelated auth-store data, missing credentials, HTTP 401/429 handling, ambiguous transport/status failures, malformed refresh responses, write failure after rotation, and concurrent refresh callers across threads and processes. Before POSTing, the adapter atomically persists a one-way fingerprint of the current refresh token; it refuses to replay that token after an uncertain response or a failed auth-store write. An accepted new token pair replaces the marker.

From the project root, with the `codex` extra installed:

```sh
.venv/bin/python -m unittest tests.test_codex_adapter -v
.venv/bin/python examples/smoke_codex.py missing-auth
.venv/bin/python examples/smoke_codex.py success
```

`success` sends one small live request and needs an existing Hermes Codex OAuth login. The narrow adapter has also been exercised from a venv where the `hermes-agent` distribution is absent.

## Observed result

The first working probe dynamically copied 117 Python modules (about 3.3 MB) just to call one text request through `agent.auxiliary_client`. That snapshot was then dropped: the direct adapter is about 11 KB and depends at runtime only on the OpenAI SDK, `httpx`, and the existing auth file. A direct non-streaming call was rejected by Codex; a typed `stream=true` call completed successfully.

The snapshot has since been **restored and moved to `vendor/hermes/`**. Dropping it was a mistake for this project: it left the Codex subscription as the only reachable provider, and adding another integration meant redoing the capture by hand. The tree is now a project asset instead of a spike artifact, with its provenance files (`MANIFEST.json`, `LICENSE.hermes`, `captured.json`) alongside it, and `tools/closure_probe.py` — the probe this folder used to hold — generalized so it can `capture`/`augment` the closure for any provider id rather than only `openai-codex`.

## Verdict: PARTIAL

The slim request path and a real subscription request work without the Hermes package. Unit tests verify atomic token rotation, including a race between separate processes; explicit 401/429 responses leave stored credentials unchanged. Before any refresh POST, the adapter persists only a hash of the current refresh token. Uncertain transport failures, unexpected HTTP statuses, malformed 200 responses, or a failed auth-store write keep that guard in place, preventing this adapter from replaying the token. A new grant can resume operation; other clients that ignore this adapter-specific marker could still try the old token, so re-authenticate with Hermes before using another client. Initial sign-in remains outside this adapter. It currently targets POSIX systems because it shares the auth store's `flock` lock.

## Update: the decision this record describes was reversed

The adapter, its tests, its smoke script, `vendor/hermes/` and `tools/closure_probe.py` have since been removed from the project. The model call now goes through pi — vendored in `vendor/pi/`, described under "The model, through pi" in the root README — which brings the provider catalogs, the credentials and the subscription OAuth with it. Neither a hand-vendored provider layer nor an adapter of our own is needed for that. The commands above name files that no longer exist; `git log` has them.
