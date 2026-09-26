"""Keep `vendor/hermes/` honest: import check, closure capture, closure repair.

The vendored tree is a copy of Hermes' modules, not an installed package, so it
can drift: a provider integration added later may import a module that was never
copied. This tool is how that is found and fixed.

    integrations                      no network: import every AI-integration entry
                                      point from the vendored tree
    capture [--provider ID]           run one real request through the *installed*
                                      Hermes and copy every Hermes module it used
                                      into vendor/hermes
    isolated [--provider ID]          same request, but with the vendored tree on
                                      sys.path and a finder that refuses to load a
                                      vendored module from anywhere else
    augment [--provider ID]           run `isolated` repeatedly, copying the next
                                      missing module from the installed Hermes,
                                      until the closure closes

`capture` and `augment` need the installed Hermes to copy from, so run them with
the Hermes interpreter. `integrations` and `isolated` work with the project venv.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENDOR = REPO / "vendor" / "hermes"
RECORD = VENDOR / "captured.json"
PROFILES = Path("plugins") / "model-providers"
DEFAULT_PROVIDER = "openai-codex"

# One implementation of "what the tree owns and how it is loaded", shared with the
# runtime (commahum.hermes_tree). The fallback covers a source tree whose package
# is not installed in the running interpreter, which is how `isolated` may be
# started from the installed Hermes.
try:
    from commahum.hermes_tree import install_fail_closed_finder, vendored_roots
except ImportError:  # pragma: no cover - source-tree fallback
    sys.path.insert(0, str(REPO / "src"))
    from commahum.hermes_tree import install_fail_closed_finder, vendored_roots

#: The AI-integration surface: sign-in machinery per provider, the provider
#: registry, the model catalogs, and the request path. All of it must import.
INTEGRATIONS = [
    "hermes_cli.auth",
    "hermes_cli.auth_constants",
    "hermes_cli.auth_codex",
    "hermes_cli.auth_openrouter",
    "hermes_cli.auth_nous",
    "hermes_cli.auth_qwen",
    "hermes_cli.auth_xai",
    "hermes_cli.auth_minimax",
    "hermes_cli.auth_zai_kimi",
    "hermes_cli.auth_spotify",
    "hermes_cli.auth_device_flow",
    "hermes_cli.auth_oauth_grants",
    "hermes_cli.auth_plugin_providers",
    "hermes_cli.auth_model_picker",
    "hermes_cli.providers",
    "hermes_cli.models",
    "hermes_cli.models_catalog_static",
    "hermes_cli.models_local",
    "hermes_cli.models_reasoning_caps",
    "hermes_cli.model_normalize",
    "hermes_cli.config",
    "hermes_cli.config_defaults",
    "hermes_cli.config_providers",
    "agent.auxiliary_client",
    "agent.codex_runtime",
    "agent.codex_responses_adapter",
    "agent.credential_pool",
    "agent.credential_persistence",
    "agent.error_classifier",
    "agent.model_metadata",
    "agent.models_dev",
    "agent.retry_utils",
    "agent.ssl_verify",
    "agent.proxy_bypass",
    "agent.secret_scope",
    "agent.reasoning_effort",
    "providers",
    "providers.base",
    "hermes_platform.host.runtime",
    "pm.paths",
    "pm.environments",
    "tools.threat_patterns",
    "utils",
    "hermes_constants",
    "hermes_time",
    "hermes_yaml",
    "hermes_bootstrap",
    "registration_lifecycle",
]

MISSING = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")


def vendored_files() -> set[str]:
    """Every module path in the tree, as the distribution would name it."""
    return {path.relative_to(VENDOR).as_posix() for path in VENDOR.rglob("*.py")}


def request(provider: str, model: str | None) -> None:
    from agent.auxiliary_client import resolve_provider_client

    client, resolved = resolve_provider_client(provider, model=model)
    if client is None:
        raise RuntimeError(f"{provider} did not resolve to a client")
    result = client.chat.completions.create(
        model=resolved,
        messages=[
            {"role": "system", "content": "Reply with exactly OK."},
            {"role": "user", "content": "ping"},
        ],
        timeout=60,
    )
    text = result.choices[0].message.content
    if text != "OK":
        raise AssertionError(f"unexpected model response: {text!r}")


def installed_files() -> tuple[set[str], Path]:
    distribution = importlib.metadata.distribution("hermes-agent")
    files = {str(path) for path in distribution.files or [] if str(path).endswith(".py")}
    return files, Path(distribution.locate_file("."))


def captured_modules(site: Path, files: set[str]) -> dict[str, Path]:
    captured = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if not path or not path.endswith(".py"):
            continue
        source = Path(path)
        try:
            relative = source.relative_to(site)
        except ValueError:
            continue
        if relative.as_posix() in files or (relative / "__init__.py").as_posix() in files:
            captured[name] = source
    return captured


def capture(provider: str, model: str | None) -> None:
    files, site = installed_files()
    request(provider, model)
    copied = set()
    for source in captured_modules(site, files).values():
        relative = source.relative_to(site)
        target = VENDOR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.add(relative.as_posix())
    # The record is the tree's inventory, not the last provider's footprint: a
    # capture for one provider must not erase what another one needed.
    known = set(copied)
    if RECORD.is_file():
        known |= set(json.loads(RECORD.read_text(encoding="utf-8")))
    RECORD.write_text(json.dumps(sorted(known), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{provider}: request OK; copied Hermes modules: {len(copied)}")
    print(f"record holds {len(known)} module paths")
    profiles = copy_profiles(site)
    print(f"provider profiles: {profiles}")
    print(f"tree now holds {len(vendored_files())} modules, "
          f"{sum(path.stat().st_size for path in VENDOR.rglob('*.py'))} bytes")


def copy_profiles(site: Path) -> int:
    """Copy the bundled provider profiles into the tree.

    They are not modules: ``providers/__init__.py`` scans
    ``<root>/plugins/model-providers/<name>/`` and executes what it finds, so no
    import ever fails when they are absent. The provider simply loses its
    profile — and with it its default model, aliases and capabilities — while
    imports keep working.
    """
    source = site / PROFILES
    if not source.is_dir():
        return 0
    target = VENDOR / PROFILES
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for path in sorted(source.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        destination = target / path.relative_to(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, destination)
        destination.chmod(0o644)
        copied += 1
    return copied


def isolated(provider: str, model: str | None) -> None:
    sys.path.insert(0, str(VENDOR))
    install_fail_closed_finder(VENDOR)
    request(provider, model)
    leaked = {
        name: str(module.__file__)
        for name, module in list(sys.modules.items())
        if (path := getattr(module, "__file__", None))
        and Path(path).suffix == ".py"
        and Path(path).parts
        and Path(path).parts[0] in vendored_roots(VENDOR)
        and not Path(path).is_relative_to(VENDOR)
    }
    if leaked:
        raise AssertionError(f"vendored names resolved outside vendor/hermes: {leaked}")
    loaded = sum(1 for module in sys.modules.values()
                 if (path := getattr(module, "__file__", None)) and Path(path).is_relative_to(VENDOR))
    print(f"{provider}: isolated request OK; modules loaded from vendor/hermes: {loaded}")


def augment(provider: str, model: str | None) -> None:
    files, site = installed_files()
    added = []
    for _ in range(60):
        result = subprocess.run(
            [sys.executable, str(Path(__file__)), "isolated", "--provider", provider, *(("--model", model) if model else ())],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode == 0:
            print(result.stdout.strip())
            print(f"lazy modules copied: {len(added)} {added}")
            return
        found = MISSING.search(result.stderr)
        if not found:
            raise RuntimeError(result.stderr[-4000:])
        module = found.group(1)
        stem = module.replace(".", "/")
        relative = next(
            (candidate for candidate in (f"{stem}.py", f"{stem}/__init__.py") if candidate in files),
            None,
        )
        if relative is None:
            raise RuntimeError(f"no installed source file for {module}")
        destination = VENDOR / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(site / relative, destination)
        added.append(relative)
        print(f"copied {relative}", flush=True)
    raise RuntimeError("closure needed more than 60 modules; something is off")


def integrations() -> int:
    """Import every integration entry point from the tree, in a fresh process."""
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    failed = []
    for name in INTEGRATIONS:
        program = (
            "import sys, importlib\n"
            f"sys.path.insert(0, {str(VENDOR)!r})\n"
            f"importlib.import_module({name!r})\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True,
            env=environment, cwd=str(REPO),
        )
        if result.returncode != 0:
            tail = (MISSING.search(result.stderr) or re.search(r"(\w*Error): (.+)", result.stderr))
            failed.append(f"{name}: {tail.group(0) if tail else result.stderr.strip().splitlines()[-1]}")
    print(f"integration entry points: {len(INTEGRATIONS)}, imported cleanly: {len(INTEGRATIONS) - len(failed)}")
    for entry in failed:
        print(f"  FAIL {entry}")
    return 1 if failed else 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=["integrations", "capture", "isolated", "augment"])
    parser.add_argument("--provider", default=DEFAULT_PROVIDER)
    parser.add_argument("--model", default=None)
    arguments = parser.parse_args(argv)

    if not VENDOR.exists():
        print(f"no vendored tree at {VENDOR}", file=sys.stderr)
        return 2
    if arguments.mode == "integrations":
        return integrations()
    {"capture": capture, "isolated": isolated, "augment": augment}[arguments.mode](
        arguments.provider, arguments.model
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
