# Spike 001: Codex path without the Hermes package

Question: can the Codex OAuth + text request path from Hermes Agent run as a
local source snapshot without installing Hermes Agent as a dependency?

## Source and rights

Upstream: https://github.com/NousResearch/hermes-agent
Source revision: `59004a62356f3a4697ab0fe8ad5086d2b405e2a6` (installed Nix build).
Files under `vendor/` are unmodified copies from that revision. See
`manifest.json` for SHA-256 of each file and `LICENSE.hermes` for the upstream
MIT license and attribution. The snapshot was captured from modules used by a
single successful text request, plus two lazily loaded modules (`tools/`).
It is not yet a complete dependency closure for every error/authentication path.

## Reproduction

The probe (`probe.py`) uses the interpreter from the installed Hermes build to
locate its source files and create the snapshot. It sends only a harmless `ping`
request; never log access or refresh tokens.

1. With an already logged-in Hermes Codex session, run
   `<hermes-python> probe.py capture` to create `vendor/`.
2. Run `<hermes-python> probe.py augment` to add modules first encountered by
   the isolated import guard. This is deliberately bounded and not a static
   dependency analysis.
3. Create a clean Python virtual environment (without the Hermes distribution).
   Install `openai`, `httpx`, `ruamel.yaml`, `python-dotenv`, `requests`, and
   `PyYAML` (pip installs their transitive dependencies).
4. From this directory, run `PYTHONPATH=vendor .venv/bin/python smoke.py missing-auth`
   and `PYTHONPATH=vendor .venv/bin/python smoke.py success`.
   The second command sends a small real Codex request and requires an existing
   Hermes OAuth login.

## Observed results

- Native Hermes client resolved and returned `OK` to an exact-output request.
- Isolated probe returned `OK` after copying 117 Python modules (3,279,651 bytes).
  Import guard confirmed no Hermes-owned module came from the installed build.
- A separate clean venv without the `hermes-agent` distribution also returned
  `OK` via the vendored files and existing Hermes OAuth login.
- With a fake `HOME` and `HERMES_HOME`, client resolution returned `(None, None)`
  rather than silently switching providers.
- The clean venv probe used Python 3.14, `openai==3.19.2` and
  `ruamel.yaml==0.19.1`; the source build had `openai==2.24.0` and
  `ruamel.yaml==0.18.16`. Compatibility outside the tested path is unknown.

## Verdict: PARTIAL

Independent importing and a real successful Codex request are demonstrated.
The current snapshot is too broad for production (it includes the 428 KB
multi-provider `agent/auxiliary_client.py`). A successful path does not prove
refresh-token handling, fresh login, 401/429 recovery, concurrency, or clean
installation on other Python versions. The copied modules still expect Hermes'
auth-store layout at runtime; the package is absent, but the user's existing
Hermes OAuth credentials were used for the successful call.

Next: statically identify the smaller function-level Codex slice, transfer
its complete source and third-party dependencies at one pinned revision, then
exercise login/refresh/error paths in a genuinely isolated test environment.
Keep the transfer script/manifest and source license in the project so an
update cannot silently mix revisions.
