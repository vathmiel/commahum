import unittest

from commahum.codex_adapter import CodexSubscriptionClient


class FakeEvents:
    def __init__(self, events):
        self.events = events

    def __iter__(self):
        return iter(self.events)


class FakeResponses:
    def __init__(self, events):
        self.events = events
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return FakeEvents(self.events)


class FakeSDKClient:
    def __init__(self, events):
        self.responses = FakeResponses(events)


class CodexSubscriptionClientTests(unittest.TestCase):
    @staticmethod
    def token_with_auth_claims():
        import base64
        import json

        payload = base64.urlsafe_b64encode(json.dumps({
            "https://api.openai.com/auth": {
                "chatgpt_account_id": "acct-123", "chatgpt_data_residency": "eu"
            }
        }).encode()).decode().rstrip("=")
        return f"header.{payload}.signature"

    def test_sends_typed_stream_request_and_returns_only_text_deltas(self):
        sdk = FakeSDKClient([
            type("Event", (), {"type": "response.created"})(),
            type("Event", (), {"type": "response.output_text.delta", "delta": "Переписанный "})(),
            type("Event", (), {"type": "response.output_text.delta", "delta": "комментарий"})(),
            type("Event", (), {"type": "response.completed"})(),
        ])
        constructed = {}

        def sdk_factory(**kwargs):
            constructed.update(kwargs)
            return sdk

        class Tokens:
            def get_access_token(inner_self):
                return self.token_with_auth_claims()

        client = CodexSubscriptionClient(
            token_store=Tokens(), sdk_client_factory=sdk_factory, model="gpt-5.5"
        )
        result = client.complete("Сделай яснее", instructions="Сохраняй смысл.")

        self.assertEqual(result, "Переписанный комментарий")
        self.assertEqual(constructed["api_key"], self.token_with_auth_claims())
        self.assertEqual(constructed["base_url"], "https://chatgpt.com/backend-api/codex")
        self.assertEqual(sdk.responses.kwargs["model"], "gpt-5.5")
        self.assertTrue(sdk.responses.kwargs["stream"])
        self.assertFalse(sdk.responses.kwargs["store"])
        self.assertEqual(sdk.responses.kwargs["instructions"], "Сохраняй смысл.")
        self.assertEqual(
            sdk.responses.kwargs["input"],
            [{"role": "user", "content": [{"type": "input_text", "text": "Сделай яснее"}]}],
        )
        self.assertEqual(constructed["default_headers"]["originator"], "commahum")
        self.assertEqual(constructed["default_headers"]["ChatGPT-Account-ID"], "acct-123")
        self.assertEqual(
            constructed["default_headers"]["x-openai-internal-codex-residency"], "eu"
        )

    def test_failed_stream_is_reported_without_returning_partial_rewrite(self):
        sdk = FakeSDKClient([
            type("Event", (), {"type": "response.output_text.delta", "delta": "partial"})(),
            type("Event", (), {"type": "response.failed"})(),
        ])

        class Tokens:
            def get_access_token(inner_self):
                return self.token_with_auth_claims()

        client = CodexSubscriptionClient(
            token_store=Tokens(), sdk_client_factory=lambda **kwargs: sdk
        )
        with self.assertRaisesRegex(RuntimeError, "Codex generation failed"):
            client.complete("text", instructions="Rewrite safely")
    def test_incomplete_stream_never_returns_partial_rewrite(self):
        sdk = FakeSDKClient([
            type("Event", (), {"type": "response.output_text.delta", "delta": "partial"})(),
        ])

        class Tokens:
            def get_access_token(inner_self):
                return self.token_with_auth_claims()

        client = CodexSubscriptionClient(
            token_store=Tokens(), sdk_client_factory=lambda **kwargs: sdk
        )
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            client.complete("text", instructions="Rewrite safely")


class FakeHTTPResponse:
    def __init__(self, status_code=200, payload=None, json_error=None):
        self.status_code = status_code
        self.headers = {}
        self.payload = (
            {"access_token": "rotated-access", "refresh_token": "rotated-refresh"}
            if payload is None else payload
        )
        self.json_error = json_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


class FakeHTTPClient:
    def __init__(self, response=None, post_error=None, **kwargs):
        self.kwargs = kwargs
        self.posts = []
        self.response = response or FakeHTTPResponse()
        self.post_error = post_error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        if self.post_error is not None:
            raise self.post_error
        return self.response


def _expired_auth_store():
    import base64
    import json

    payload = base64.urlsafe_b64encode(json.dumps({"exp": 99}).encode()).decode().rstrip("=")
    access_token = f"header.{payload}.signature"
    return json.dumps({"providers": {
        "openai-codex": {"tokens": {
            "access_token": access_token, "refresh_token": "single-use-refresh"
        }}
    }}).encode()


def _cross_process_refresh_worker(auth_path, barrier, calls, results):
    import time
    from pathlib import Path

    from commahum.codex_adapter import HermesTokenStore

    class DelayedHTTPClient(FakeHTTPClient):
        def post(self, url, **kwargs):
            with calls.get_lock():
                calls.value += 1
            time.sleep(0.5)
            return super().post(url, **kwargs)

    barrier.wait(timeout=5)
    store = HermesTokenStore(
        auth_path=Path(auth_path),
        clock=lambda: 100,
        http_client_factory=lambda **kwargs: DelayedHTTPClient(**kwargs),
    )
    results.put(store.get_access_token())


class HermesTokenStoreTests(unittest.TestCase):
    def _store_with_http_client(self, auth_path, http_client):
        from commahum.codex_adapter import HermesTokenStore

        original = _expired_auth_store()
        auth_path.write_bytes(original)
        store = HermesTokenStore(
            auth_path=auth_path,
            clock=lambda: 100,
            http_client_factory=lambda **kwargs: http_client,
        )
        return store, original

    def _assert_ambiguous_refresh_guard(self, client):
        import hashlib
        import json
        import tempfile
        from pathlib import Path

        from commahum.codex_adapter import CodexAuthError

        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            store, original = self._store_with_http_client(auth_path, client)

            with self.assertRaisesRegex(CodexAuthError, "outcome is unknown"):
                store.get_access_token()

            self.assertEqual(len(client.posts), 1)
            state = json.loads(auth_path.read_text())["providers"]["openai-codex"]
            self.assertEqual(state["tokens"]["refresh_token"], "single-use-refresh")
            self.assertEqual(
                state["commahum_refresh_uncertain_refresh_sha256"],
                hashlib.sha256(b"single-use-refresh").hexdigest(),
            )
            self.assertNotEqual(auth_path.read_bytes(), original)
            with self.assertRaisesRegex(CodexAuthError, "sign in again with Hermes"):
                store.get_access_token()
            self.assertEqual(len(client.posts), 1)

    def test_invalid_json_refresh_response_guards_token_replay(self):
        import json

        self._assert_ambiguous_refresh_guard(
            FakeHTTPClient(
                response=FakeHTTPResponse(
                    json_error=json.JSONDecodeError("invalid JSON", "{", 0)
                )
            )
        )

    def test_non_object_refresh_response_guards_token_replay(self):
        self._assert_ambiguous_refresh_guard(
            FakeHTTPClient(response=FakeHTTPResponse(payload=[]))
        )

    def test_refresh_response_without_access_token_guards_token_replay(self):
        self._assert_ambiguous_refresh_guard(
            FakeHTTPClient(response=FakeHTTPResponse(
                payload={"access_token": "", "refresh_token": "rotated-refresh"}
            ))
        )

    def test_refresh_response_with_malformed_refresh_token_guards_replay(self):
        self._assert_ambiguous_refresh_guard(
            FakeHTTPClient(response=FakeHTTPResponse(
                payload={"access_token": "rotated-access", "refresh_token": 42}
            ))
        )

    def test_refresh_response_without_refresh_token_guards_replay(self):
        self._assert_ambiguous_refresh_guard(
            FakeHTTPClient(response=FakeHTTPResponse(
                payload={"access_token": "rotated-access"}
            ))
        )

    def test_ambiguous_http_statuses_guard_token_replay(self):
        for status_code in (408, 500, 503):
            with self.subTest(status_code=status_code):
                self._assert_ambiguous_refresh_guard(
                    FakeHTTPClient(response=FakeHTTPResponse(status_code=status_code))
                )

    def test_post_submit_read_errors_guard_token_replay(self):
        import httpx

        errors = (
            httpx.ReadError("response connection reset"),
            httpx.RemoteProtocolError("malformed response stream"),
        )
        for error in errors:
            with self.subTest(error=type(error).__name__):
                self._assert_ambiguous_refresh_guard(FakeHTTPClient(post_error=error))

    def test_http_401_fails_without_modifying_or_retrying_tokens(self):
        import json
        import tempfile
        from pathlib import Path

        from commahum.codex_adapter import CodexAuthError

        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            client = FakeHTTPClient(response=FakeHTTPResponse(status_code=401))
            store, original = self._store_with_http_client(auth_path, client)

            with self.assertRaisesRegex(CodexAuthError, "sign in again with Hermes"):
                store.get_access_token()

            self.assertEqual(len(client.posts), 1)
            self.assertEqual(json.loads(auth_path.read_text()), json.loads(original))

    def test_http_429_fails_without_modifying_or_retrying_tokens(self):
        import json
        import tempfile
        from pathlib import Path

        from commahum.codex_adapter import CodexAuthError

        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            client = FakeHTTPClient(response=FakeHTTPResponse(status_code=429))
            store, original = self._store_with_http_client(auth_path, client)

            with self.assertRaisesRegex(CodexAuthError, "rate limited; retry later"):
                store.get_access_token()

            self.assertEqual(len(client.posts), 1)
            self.assertEqual(json.loads(auth_path.read_text()), json.loads(original))

    def test_ambiguous_refresh_timeout_persists_guard_and_prevents_replay(self):
        import hashlib
        import json
        import tempfile
        from pathlib import Path

        import httpx
        from commahum.codex_adapter import CodexAuthError

        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            timeout = httpx.ReadTimeout("refresh response timed out")
            client = FakeHTTPClient(post_error=timeout)
            store, _ = self._store_with_http_client(auth_path, client)

            with self.assertRaisesRegex(CodexAuthError, "outcome is unknown") as caught:
                store.get_access_token()

            self.assertIs(caught.exception.__cause__.__cause__, timeout)
            self.assertEqual(len(client.posts), 1)
            saved = json.loads(auth_path.read_text())
            state = saved["providers"]["openai-codex"]
            self.assertEqual(state["tokens"]["refresh_token"], "single-use-refresh")
            self.assertEqual(
                state["commahum_refresh_uncertain_refresh_sha256"],
                hashlib.sha256(b"single-use-refresh").hexdigest(),
            )
            with self.assertRaisesRegex(CodexAuthError, "sign in again with Hermes"):
                store.get_access_token()
            self.assertEqual(len(client.posts), 1)

            auth_path.write_bytes(
                _expired_auth_store().replace(b"single-use-refresh", b"fresh-refresh")
            )
            client.post_error = None
            self.assertEqual(store.get_access_token(), "rotated-access")
            self.assertEqual(len(client.posts), 2)
            recovered = json.loads(auth_path.read_text())["providers"]["openai-codex"]
            self.assertNotIn("commahum_refresh_uncertain_refresh_sha256", recovered)

    def test_failed_auth_write_after_rotation_keeps_guard_before_replay(self):
        import hashlib
        import json
        import tempfile
        from pathlib import Path

        from commahum.codex_adapter import CodexAuthError

        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            client = FakeHTTPClient()
            store, _ = self._store_with_http_client(auth_path, client)
            original_write = store._write_store
            write_count = 0

            def fail_second_write(auth_store):
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise OSError("simulated disk write failure after refresh")
                original_write(auth_store)

            store._write_store = fail_second_write
            with self.assertRaisesRegex(OSError, "simulated disk write failure"):
                store.get_access_token()

            self.assertEqual(len(client.posts), 1)
            saved = json.loads(auth_path.read_text())["providers"]["openai-codex"]
            self.assertEqual(saved["tokens"]["refresh_token"], "single-use-refresh")
            self.assertEqual(
                saved["commahum_refresh_uncertain_refresh_sha256"],
                hashlib.sha256(b"single-use-refresh").hexdigest(),
            )

            store._write_store = original_write
            with self.assertRaisesRegex(CodexAuthError, "sign in again with Hermes"):
                store.get_access_token()
            self.assertEqual(len(client.posts), 1)

    def test_refreshes_expired_token_and_atomically_preserves_other_auth_state(self):
        import base64
        import json
        import tempfile
        from pathlib import Path

        from commahum.codex_adapter import HermesTokenStore

        def token_with_exp(exp):
            payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
            return f"header.{payload}.signature"

        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            auth_path.write_text(json.dumps({
                "version": 1,
                "active_provider": "nous",
                "providers": {
                    "openai-codex": {"tokens": {
                        "access_token": token_with_exp(99),
                        "refresh_token": "old-refresh",
                        "account_id": "keep-account",
                    }},
                    "unrelated": {"secret": "keep-this-state"},
                },
            }))
            http_client = FakeHTTPClient()
            store = HermesTokenStore(
                auth_path=auth_path,
                clock=lambda: 100,
                http_client_factory=lambda **kwargs: http_client,
            )

            self.assertEqual(store.get_access_token(), "rotated-access")
            self.assertEqual(len(http_client.posts), 1)
            self.assertEqual(http_client.posts[0][1]["data"]["refresh_token"], "old-refresh")
            saved = json.loads(auth_path.read_text())
            tokens = saved["providers"]["openai-codex"]["tokens"]
            self.assertEqual(tokens["refresh_token"], "rotated-refresh")
            self.assertEqual(tokens["account_id"], "keep-account")
            self.assertEqual(saved["active_provider"], "nous")
            self.assertEqual(saved["providers"]["unrelated"]["secret"], "keep-this-state")
            self.assertEqual(auth_path.stat().st_mode & 0o777, 0o600)

    def test_missing_codex_state_fails_without_falling_back_to_other_provider(self):
        import json
        import tempfile
        from pathlib import Path

        from commahum.codex_adapter import CodexAuthError, HermesTokenStore

        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            auth_path.write_text(json.dumps({"providers": {"nous": {"token": "not-codex"}}}))
            store = HermesTokenStore(auth_path=auth_path, http_client_factory=lambda **kwargs: self.fail())
            with self.assertRaises(CodexAuthError):
                store.get_access_token()

    def test_concurrent_callers_rotate_refresh_token_only_once(self):
        import base64
        import json
        import tempfile
        import threading
        import time
        from concurrent.futures import ThreadPoolExecutor
        from pathlib import Path

        from commahum.codex_adapter import HermesTokenStore

        payload = base64.urlsafe_b64encode(json.dumps({"exp": 99}).encode()).decode().rstrip("=")
        expired = f"header.{payload}.signature"
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            auth_path.write_text(json.dumps({"providers": {
                "openai-codex": {"tokens": {
                    "access_token": expired, "refresh_token": "single-use-refresh"
                }}
            }}))
            count_lock = threading.Lock()
            calls = 0

            class DelayedHTTPClient(FakeHTTPClient):
                def post(self, url, **kwargs):
                    nonlocal calls
                    with count_lock:
                        calls += 1
                    time.sleep(0.05)
                    return super().post(url, **kwargs)

            factory = lambda **kwargs: DelayedHTTPClient(**kwargs)
            stores = [
                HermesTokenStore(auth_path=auth_path, clock=lambda: 100, http_client_factory=factory)
                for _ in range(2)
            ]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda store: store.get_access_token(), stores))

            self.assertEqual(results, ["rotated-access", "rotated-access"])
            self.assertEqual(calls, 1)

    def test_separate_processes_rotate_refresh_token_only_once(self):
        import base64
        import json
        import multiprocessing
        import tempfile
        from pathlib import Path

        context = multiprocessing.get_context("fork")
        payload = base64.urlsafe_b64encode(json.dumps({"exp": 99}).encode()).decode().rstrip("=")
        expired = f"header.{payload}.signature"
        with tempfile.TemporaryDirectory() as temp_dir:
            auth_path = Path(temp_dir) / "auth.json"
            auth_path.write_text(json.dumps({"providers": {
                "openai-codex": {"tokens": {
                    "access_token": expired, "refresh_token": "single-use-refresh"
                }}
            }}))
            barrier = context.Barrier(2)
            calls = context.Value("i", 0)
            results = context.Queue()
            processes = [
                context.Process(
                    target=_cross_process_refresh_worker,
                    args=(auth_path, barrier, calls, results),
                )
                for _ in range(2)
            ]
            try:
                for process in processes:
                    process.start()
                for process in processes:
                    process.join(timeout=10)

                self.assertFalse(any(process.is_alive() for process in processes))
                self.assertEqual([process.exitcode for process in processes], [0, 0])
                self.assertEqual(
                    sorted(results.get(timeout=1) for _ in processes),
                    ["rotated-access", "rotated-access"],
                )
                self.assertEqual(calls.value, 1)
            finally:
                for process in processes:
                    if process.is_alive():
                        process.terminate()
                        process.join()
                results.close()
                results.join_thread()


if __name__ == "__main__":
    unittest.main()
