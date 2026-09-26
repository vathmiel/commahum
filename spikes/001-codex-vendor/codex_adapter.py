"""Minimal Codex-subscription text client, derived from Hermes Agent's Codex path."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
DEFAULT_MODEL = "gpt-5.5"
USER_AGENT = "commahum/0.1"
ORIGINATOR = "commahum"
CODEX_OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
CODEX_OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
REFRESH_UNCERTAIN_KEY = "commahum_refresh_uncertain_refresh_sha256"


class CodexAuthError(RuntimeError):
    """Missing or unusable Codex subscription credentials."""


class _CodexRefreshOutcomeUnknown(CodexAuthError):
    """The refresh may have been consumed, but its response was not received."""


class HermesTokenStore:
    """Read and refresh Codex credentials in Hermes' auth.json format.

    The `.lock` filename and exclusive flock match Hermes' auth-store convention,
    preventing this client from racing Hermes while rotating single-use refresh tokens.
    """

    def __init__(
        self,
        *,
        auth_path: Optional[Path] = None,
        clock: Callable[[], float] = time.time,
        http_client_factory: Optional[Callable[..., Any]] = None,
        refresh_skew_seconds: int = 120,
    ) -> None:
        if auth_path is None:
            hermes_home = os.environ.get("HERMES_HOME", "").strip()
            base = Path(hermes_home).expanduser() if hermes_home else Path.home() / ".hermes"
            auth_path = base / "auth.json"
        self._auth_path = Path(auth_path).expanduser()
        self._clock = clock
        self._http_client_factory = http_client_factory
        self._refresh_skew_seconds = max(0, int(refresh_skew_seconds))

    def get_access_token(self) -> str:
        with _auth_file_lock(self._auth_path):
            store = self._read_store()
            providers = store.get("providers")
            state = providers.get("openai-codex") if isinstance(providers, dict) else None
            tokens = state.get("tokens") if isinstance(state, dict) else None
            if not isinstance(tokens, dict):
                raise CodexAuthError("No Codex subscription credentials found in Hermes auth.json")
            access = tokens.get("access_token")
            refresh = tokens.get("refresh_token")
            if not isinstance(access, str) or not access.strip():
                raise CodexAuthError("Codex auth store has no access token; sign in again with Hermes")
            if not isinstance(refresh, str) or not refresh.strip():
                raise CodexAuthError("Codex auth store has no refresh token; sign in again with Hermes")

            expires_at = _jwt_expiration(access)
            if expires_at is None or expires_at > self._clock() + self._refresh_skew_seconds:
                return access

            refresh_token = refresh.strip()
            if state.get(REFRESH_UNCERTAIN_KEY) == _token_fingerprint(refresh_token):
                raise CodexAuthError(
                    "Previous Codex token refresh outcome is unknown; "
                    "sign in again with Hermes before retrying"
                )
            state[REFRESH_UNCERTAIN_KEY] = _token_fingerprint(refresh_token)
            self._write_store(store)
            try:
                payload = self._refresh(refresh_token)
            except _CodexRefreshOutcomeUnknown as exc:
                raise CodexAuthError(
                    "Codex token refresh outcome is unknown; "
                    "sign in again with Hermes before retrying"
                ) from exc
            except CodexAuthError:
                state.pop(REFRESH_UNCERTAIN_KEY, None)
                try:
                    self._write_store(store)
                except OSError as persist_error:
                    raise CodexAuthError(
                        "Could not clear the pending Codex refresh guard; "
                        "sign in again with Hermes"
                    ) from persist_error
                raise
            next_access = payload.get("access_token")
            if not isinstance(next_access, str) or not next_access.strip():
                raise CodexAuthError("Codex token refresh returned no access token")
            tokens["access_token"] = next_access.strip()
            next_refresh = payload.get("refresh_token")
            if isinstance(next_refresh, str) and next_refresh.strip():
                tokens["refresh_token"] = next_refresh.strip()
            state.pop(REFRESH_UNCERTAIN_KEY, None)
            state["last_refresh"] = datetime.now(timezone.utc).isoformat()
            self._write_store(store)
            return tokens["access_token"]

    def _read_store(self) -> dict[str, Any]:
        try:
            value = json.loads(self._auth_path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError as exc:
            raise CodexAuthError("Hermes auth.json not found; sign in to Codex with Hermes first") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise CodexAuthError("Could not read Hermes auth.json safely") from exc
        if not isinstance(value, dict):
            raise CodexAuthError("Hermes auth.json has an invalid structure")
        return value

    def _refresh(self, refresh_token: str) -> dict[str, Any]:
        import httpx

        factory = self._http_client_factory
        if factory is None:
            factory = httpx.Client
        try:
            with factory(timeout=20.0, headers={"Accept": "application/json", "User-Agent": USER_AGENT}) as client:
                response = client.post(
                    CODEX_OAUTH_TOKEN_URL,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": CODEX_OAUTH_CLIENT_ID,
                    },
                )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            raise CodexAuthError("Codex token refresh request could not be sent") from exc
        except Exception as exc:
            raise _CodexRefreshOutcomeUnknown(
                "Codex refresh request completed without a trustworthy response"
            ) from exc
        if response.status_code == 429:
            raise CodexAuthError("Codex token refresh is rate limited; retry later")
        if response.status_code == 401:
            raise CodexAuthError("Codex token refresh failed; sign in again with Hermes")
        if response.status_code != 200:
            raise _CodexRefreshOutcomeUnknown(
                f"Codex refresh returned ambiguous HTTP status {response.status_code}"
            )
        try:
            payload = response.json()
        except Exception as exc:
            raise _CodexRefreshOutcomeUnknown("Codex refresh returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise _CodexRefreshOutcomeUnknown("Codex refresh returned an invalid response")
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            raise _CodexRefreshOutcomeUnknown("Codex refresh response was missing access_token")
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token.strip():
            raise _CodexRefreshOutcomeUnknown("Codex refresh response was missing refresh_token")
        return payload

    def _write_store(self, store: dict[str, Any]) -> None:
        self._auth_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp_name = tempfile.mkstemp(prefix=".auth-", suffix=".json", dir=self._auth_path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(store, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self._auth_path)
            directory_fd = os.open(self._auth_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise


@contextmanager
def _auth_file_lock(auth_path: Path):
    import fcntl

    auth_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_path = auth_path.with_suffix(".lock")
    with lock_path.open("a+") as stream:
        os.chmod(lock_path, 0o600)
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload_part = token.split(".")[1]
        raw = base64.urlsafe_b64decode(payload_part + "=" * (-len(payload_part) % 4))
        payload = json.loads(raw)
        return payload if isinstance(payload, dict) else {}
    except (IndexError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _token_fingerprint(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _jwt_expiration(token: str) -> Optional[float]:
    exp = _jwt_claims(token).get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


class CodexSubscriptionClient:
    """Send one text-only prompt through the Codex Responses streaming endpoint."""

    def __init__(
        self,
        *,
        token_store: Any,
        sdk_client_factory: Optional[Callable[..., Any]] = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self._token_store = token_store
        self._sdk_client_factory = sdk_client_factory
        self._model = model

    def complete(self, prompt: str, *, instructions: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("prompt must be non-empty text")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("instructions must be non-empty text")

        access_token = self._token_store.get_access_token()
        client_factory = self._sdk_client_factory
        if client_factory is None:
            from openai import OpenAI
            client_factory = OpenAI

        client = client_factory(
            api_key=access_token,
            base_url=CODEX_BASE_URL,
            default_headers={
                "User-Agent": USER_AGENT,
                "originator": ORIGINATOR,
                **_account_headers(access_token),
            },
        )
        stream = None
        try:
            stream = client.responses.create(
                model=self._model,
                instructions=instructions,
                input=[{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                store=False,
                stream=True,
            )
            chunks = []
            completed = False
            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type in {"response.failed", "response.incomplete", "error"}:
                    raise RuntimeError("Codex generation failed")
                if event_type == "response.output_text.delta":
                    delta = getattr(event, "delta", None)
                    if isinstance(delta, str):
                        chunks.append(delta)
                elif event_type == "response.completed":
                    completed = True
            if not completed:
                raise RuntimeError("Codex response did not complete")
            text = "".join(chunks).strip()
            if not text:
                raise RuntimeError("Codex returned no output text")
            return text
        finally:
            close_stream = getattr(stream, "close", None)
            if callable(close_stream):
                close_stream()
            close_client = getattr(client, "close", None)
            if callable(close_client):
                close_client()


def _account_headers(access_token: str) -> dict[str, str]:
    """Extract the account ID and data-residency hints from the Codex access JWT."""
    auth = _jwt_claims(access_token).get("https://api.openai.com/auth", {})
    headers = {}
    account_id = auth.get("chatgpt_account_id")
    if isinstance(account_id, str) and account_id:
        headers["ChatGPT-Account-ID"] = account_id
    residency = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
    if isinstance(residency, str) and residency.strip():
        headers["x-openai-internal-codex-residency"] = residency.strip()
    return headers
