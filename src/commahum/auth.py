"""The `auth` command: what pi will answer with, and where the key it uses lives.

Credentials are pi's business and this project keeps no second copy of them. What
this command adds is the two things a run needs that pi's own CLI does not put in
one place.

*Where the credential came from.* `pi auth check` answers ready or not_ready but
never says whether the key came out of its store or out of the shell, and that is
exactly the question when a run works on one machine and not another. So the answer
is computed the way this project computes anything it cannot see: ask once with the
environment as it is, then ask again with every provider variable taken away. Ready
in both means pi's own store; ready only in the first means the environment; not
ready in either is not ready, and pi's word for the reason is printed.

*A key into pi's store without the TUI,* which is otherwise the only path pi offers
for an API key. `--store` reads the key from stdin, a named variable or a file —
never from the command line, which the process table displays — writes pi's
auth.json the way pi writes it (atomically, mode 600, keeping every other
provider), and then asks pi whether it works. A store we wrote that pi does not
accept is put back as it was and reported as a failure, because a credential is
either confirmed by pi or it is not a credential.

Subscription logins are not reimplemented here: `--login` starts pi's own interface,
where `/login` runs a flow we cannot copy and keeps refreshing its tokens afterwards.

    commahum auth                                  # what pi is ready for, and from where
    commahum auth --provider deepseek              # one provider, asked about by name
    commahum auth --store deepseek --from-env DEEPSEEK_API_KEY
    commahum auth --store deepseek --from-file ~/.hermes/.env --from-var DEEPSEEK_API_KEY
    commahum auth --login                          # pi's own /login, for a subscription
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile

from .pi_runner import ENVIRONMENT, pi_command

__all__ = [
    "AuthError",
    "add_arguments",
    "bare_environment",
    "check",
    "key_from",
    "login_argv",
    "provider_of",
    "run",
    "source_of",
    "store_path",
    "stored_providers",
]

#: pi's own switch for the directory that holds auth.json. Honoured here so that a
#: caller can point pi at another store — which is also how the tests stay away
#: from the live one.
AGENT_DIR_VARIABLE = "PI_CODING_AGENT_DIR"

#: The store pi reads, relative to the home directory, when that switch is unset.
DEFAULT_AGENT_DIR = (".pi", "agent")

#: A check is local for an API key; a subscription may refresh its token first.
CHECK_TIMEOUT_SECONDS = 120

#: What pi needs to find its own store — and nothing that carries a key, or the
#: second check would pass for the reason the first one passed.
KEPT_VARIABLES = ("HOME", "PATH", "TERM", AGENT_DIR_VARIABLE)


class AuthError(RuntimeError):
    """The command cannot do what it was asked, and says so in one sentence."""


def store_path() -> pathlib.Path:
    """Where pi keeps its credentials: its own directory switch, else ``~/.pi/agent``."""
    directory = os.environ.get(AGENT_DIR_VARIABLE)
    base = pathlib.Path(directory).expanduser() if directory else pathlib.Path.home().joinpath(*DEFAULT_AGENT_DIR)
    return base / "auth.json"


def bare_environment() -> dict:
    """pi's minimal environment: enough to find its store, no provider variables.

    The quiet switches pi documents travel along, so a check does not chatter about
    versions or telemetry; ``PI_OFFLINE`` deliberately does not, because it would
    also stop pi from refreshing a subscription's own token.
    """
    environment = {name: os.environ[name] for name in KEPT_VARIABLES if name in os.environ}
    environment.update(ENVIRONMENT)
    return environment


def provider_of(arguments) -> str | None:
    """``--provider`` names one; so does the provider part of ``--model provider/id``."""
    if arguments.provider:
        return arguments.provider
    model = arguments.model or ""
    return model.split("/", 1)[0] if "/" in model else None


def check(provider: str, *, environment: dict | None = None) -> dict:
    """Ask pi itself whether it can answer for ``provider``.

    ``environment=None`` means the shell as it is; :func:`bare_environment` is the
    same question with the provider variables removed. pi's answer is returned as
    it wrote it, except when it wrote something that is not JSON — that becomes an
    honest ``unknown`` with the last thing it said, rather than a guess.
    """
    command = [*pi_command(), "auth", "check", "--provider", provider, "--json"]
    completed = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=environment,
        timeout=CHECK_TIMEOUT_SECONDS,
    )
    try:
        verdict = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {"status": "unknown", "provider": provider, "reason": _last_line(completed)}
    return verdict if isinstance(verdict, dict) else {"status": "unknown", "provider": provider}


def source_of(provider: str, *, checker=None) -> tuple[str, dict]:
    """``(source, pi's answer)`` for ``provider``, where source is one of:

    ``store``        ready even with the environment emptied — pi holds it
    ``environment``  ready as things are, not ready without the shell's variables
    ``none``         pi says it cannot answer
    ``unknown``      pi's second answer was not a verdict at all

    ``checker`` replaces the question to pi, and is looked up at call time so that
    a test can ask these functions without a pi anywhere near them.
    """
    checker = checker or check
    as_is = checker(provider)
    if as_is.get("status") != "ready":
        return "none", as_is
    bare = checker(provider, environment=bare_environment())
    if bare.get("status") == "ready":
        return "store", as_is
    if bare.get("status") == "unknown":
        return "unknown", as_is
    return "environment", as_is


def stored_providers() -> list[str]:
    """The providers pi's store has entries for, in a stable order."""
    path = store_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return []
    return sorted(data) if isinstance(data, dict) else []


def key_from(text: str, *, name: str | None = None) -> str:
    """The key out of a file or a pipe: a bare line, or the value of ``NAME=value``.

    A key usually travels in a dotenv file that holds several of them, so a name
    can be asked for and then only its line counts. Without a name the first
    key-shaped line wins, which is what a file holding just the key has. Surrounding
    whitespace and one layer of quotes come off. Nothing found is an error rather
    than an empty entry in the store, which would look like a credential and behave
    like none.
    """
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key_name, separator, value = stripped.partition("=")
        if name is not None:
            if not separator or key_name.strip() != name:
                continue
            candidate = value.strip().strip("\"'")
        else:
            candidate = (value if separator else stripped).strip().strip("\"'")
        if candidate:
            return candidate
    if name is not None:
        raise AuthError(f"no line for {name} in the input")
    raise AuthError("no key in the input")


def source_text(arguments) -> str:
    """Where the key comes from — never argv, which the process table shows."""
    if arguments.from_env:
        if arguments.from_env not in os.environ:
            raise AuthError(f"{arguments.from_env} is not set in the environment")
        return os.environ[arguments.from_env]
    if arguments.from_file:
        path = pathlib.Path(arguments.from_file).expanduser()
        if not path.is_file():
            raise AuthError(f"no such file: {path}")
        return path.read_text(encoding="utf-8")
    if sys.stdin.isatty():
        raise AuthError(
            "read the key from stdin, or name a source with --from-env/--from-file; "
            "a command line is not a place for a credential"
        )
    return sys.stdin.read()


def login_argv(arguments) -> list[str]:
    """pi's own command line, carrying the model hints the user gave and nothing else."""
    command = list(pi_command())
    if arguments.provider:
        command += ["--provider", arguments.provider]
    if arguments.model:
        command += ["--model", arguments.model]
    return command


def _load(path: pathlib.Path) -> dict:
    """The store as it stands, refusing to touch a file that is not one."""
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise AuthError(f"{path} is not valid JSON ({error}); refusing to overwrite it") from error
    if not isinstance(data, dict):
        raise AuthError(f"{path} does not hold an object; refusing to overwrite it")
    return data


def _write(path: pathlib.Path, data: dict) -> None:
    """Replace the store in one step, holding what pi itself would hold."""
    _write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: pathlib.Path, text: str) -> None:
    """Replace a file in one step, with the mode pi itself gives its store (0600)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(prefix=".auth.json.", dir=str(path.parent))
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def add_arguments(parser) -> None:
    parser.add_argument("--provider", default=None, help="ask about this provider (deepseek, anthropic, ...)")
    parser.add_argument("--model", default=None, help="ask about the provider of this model (provider/id)")
    parser.add_argument(
        "--store",
        metavar="PROVIDER",
        default=None,
        help="write a key for this provider into pi's store, and make pi confirm it",
    )
    parser.add_argument(
        "--from-env",
        metavar="NAME",
        default=None,
        help="read the key from this environment variable (with --store)",
    )
    parser.add_argument(
        "--from-file",
        metavar="PATH",
        default=None,
        help="read the key from this file, a bare key or a NAME=value line (with --store)",
    )
    parser.add_argument(
        "--from-var",
        metavar="NAME",
        default=None,
        help="the NAME to take from that file or pipe, when it holds more than one key (with --store)",
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="start pi's own interface, where /login signs a subscription in",
    )


def run(arguments, generate=None) -> int:
    """The generator is not used: this command asks pi about pi, not a model about prose."""
    if arguments.login and arguments.store:
        raise AuthError("--login and --store are two different things; ask for one of them")
    if arguments.login:
        return login(arguments)
    if arguments.store:
        return store(arguments, provider=arguments.store)
    if arguments.from_env or arguments.from_file:
        raise AuthError("--from-env and --from-file belong with --store")
    if not arguments.provider and arguments.model and "/" not in arguments.model:
        raise AuthError(f"--model {arguments.model} does not name a provider; use --model provider/id, or --provider")

    provider = provider_of(arguments)
    providers = [provider] if provider else stored_providers()
    if not providers:
        print(
            f"pi's store is empty ({store_path()}): put an API key in with "
            "`commahum auth --store <provider>`, or sign a subscription in with `commahum auth --login`"
        )
        return 1

    failures = 0
    for name in providers:
        source, verdict = source_of(name)
        failures += source in ("none", "unknown")
        if source == "store":
            print(f"{name}: ready — the credential is in pi's store ({_store_mode()})")
        elif source == "environment":
            print(f"{name}: ready — the credential comes from the environment")
        elif source == "unknown":
            print(f"{name}: pi did not answer as expected — {verdict.get('reason')}")
        else:
            reason = verdict.get("reason") or verdict.get("status")
            print(
                f"{name}: not ready — {reason}; store an API key with "
                f"`commahum auth --store {name}`, or sign in with `commahum auth --login`"
            )
    return 1 if failures else 0


def store(arguments, *, provider: str, checker=None) -> int:
    """Put a key into pi's store, and let pi be the one who says whether it works."""
    checker = checker or check
    key = key_from(source_text(arguments), name=arguments.from_var)
    path = store_path()
    previous = path.read_text(encoding="utf-8") if path.is_file() else None

    data = dict(_load(path) if previous is not None else {})
    data[provider] = {"type": "api_key", "key": key}
    _write(path, data)

    verdict = checker(provider, environment=bare_environment())
    if verdict.get("status") != "ready":
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            _write_text(path, previous)
        raise AuthError(
            f"pi did not confirm the key for {provider} "
            f"({verdict.get('reason') or verdict.get('status')}); the store is back as it was"
        )

    print(f"{provider}: the key is in {_store_mode()}, and pi confirms it; {len(key)} characters were never shown")
    return 0


def login(arguments) -> int:
    """Hand the terminal to pi, whose ``/login`` runs the flow and refreshes tokens after."""
    command = login_argv(arguments)
    print(f"starting pi: {' '.join(command)}\nsign in with /login\n", file=sys.stderr, flush=True)
    os.execvp(command[0], command)
    return 0  # pragma: no cover - execvp does not return


def _store_mode() -> str:
    """``path, mode 600`` for the store, or just the path if it cannot be read."""
    path = store_path()
    try:
        return f"{path}, mode {oct(path.stat().st_mode & 0o777)}"
    except OSError:
        return str(path)


def _last_line(completed) -> str:
    text = (completed.stderr or completed.stdout or "").strip()
    return text.splitlines()[-1] if text else "pi said nothing"
