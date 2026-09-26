"""Throwaway probe: capture the Hermes modules used by one Codex text call.

Run with the installed Hermes interpreter. Never print tokens or response headers.
This is a feasibility experiment, NOT the production dependency closure.
"""
from __future__ import annotations

import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
DIST = importlib.metadata.distribution("hermes-agent")
FILES = {str(p) for p in DIST.files or [] if str(p).endswith(".py")}
SITE = Path(DIST.locate_file("."))


def request() -> str:
    from agent.auxiliary_client import resolve_provider_client

    client, model = resolve_provider_client("openai-codex", model="gpt-5.5")
    if client is None:
        raise RuntimeError("Codex OAuth client was not resolved")
    result = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Reply with exactly OK."},
            {"role": "user", "content": "ping"},
        ],
        timeout=60,
    )
    text = result.choices[0].message.content
    if text != "OK":
        raise AssertionError(f"Unexpected model response: {text!r}")
    return text


def captured_modules() -> dict[str, Path]:
    result = {}
    for name, module in list(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if path and path.endswith(".py"):
            source = Path(path)
            try:
                relative = source.relative_to(SITE)
            except ValueError:
                continue
            if relative.as_posix() in FILES:
                result[name] = source
    return result


def capture() -> None:
    request()
    modules = captured_modules()
    copied = set()
    for source in modules.values():
        relative = source.relative_to(SITE)
        target = VENDOR / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        copied.add(relative.as_posix())
    (ROOT / "captured.json").write_text(
        json.dumps(sorted(copied), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Native call: OK; captured Hermes Python modules: {len(copied)}")
    print(f"Total copied bytes: {sum((VENDOR / name).stat().st_size for name in copied)}")


def isolated() -> None:
    # The interpreter still has third-party dependencies from Hermes' environment.
    # Fail closed on any Hermes-owned import that would resolve outside vendor/.
    import importlib.abc

    names = set()
    for file in FILES:
        part = file.removesuffix(".py")
        names.add(part.removesuffix("/__init__").replace("/", "."))

    class NoInstalledHermes(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname in names:
                relative = Path(*fullname.split("."))
                if not (VENDOR / relative.with_suffix(".py")).exists() and not (VENDOR / relative / "__init__.py").exists():
                    raise ModuleNotFoundError(f"Hermes module absent from isolated vendor: {fullname}")
            return None

    sys.path.insert(0, str(VENDOR))
    sys.meta_path.insert(0, NoInstalledHermes())
    request()
    leaks = {name: str(path) for name, path in captured_modules().items()}
    if leaks:
        raise AssertionError(f"Hermes modules resolved outside vendor: {leaks}")
    vendored = {
        name for name, module in sys.modules.items()
        if (path := getattr(module, "__file__", None)) and Path(path).is_relative_to(VENDOR)
    }
    print(f"Isolated vendor call: OK; loaded vendored modules: {len(vendored)}")


def augment() -> None:
    """Add lazy-loaded dependencies one at a time; fail if the closure grows unbounded."""
    missing = []
    for _ in range(60):
        result = subprocess.run(
            [sys.executable, str(Path(__file__)), "isolated"],
            capture_output=True, text=True, timeout=100,
        )
        if result.returncode == 0:
            print(result.stdout.strip())
            print(f"Lazy-loaded modules added: {len(missing)}")
            print(f"Modules: {len(list(VENDOR.rglob('*.py')))}")
            print(f"Bytes: {sum(p.stat().st_size for p in VENDOR.rglob('*.py'))}")
            return
        match = re.search(r"Hermes module absent from isolated vendor: ([\w.]+)", result.stderr)
        if not match:
            raise RuntimeError(result.stderr[-4000:])
        module = match.group(1)
        stem = module.replace(".", "/")
        relative = next((p for p in (f"{stem}.py", f"{stem}/__init__.py") if p in FILES), None)
        if not relative:
            raise RuntimeError(f"No source file for {module}")
        destination = VENDOR / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(SITE / relative, destination)
        missing.append(relative)
        print(f"added {relative}", flush=True)
    raise RuntimeError("Dependency closure exceeded 60 lazy-loaded modules")


if __name__ == "__main__":
    if len(sys.argv) != 2 or sys.argv[1] not in {"capture", "isolated", "augment"}:
        raise SystemExit("usage: probe.py capture|isolated|augment")
    {"capture": capture, "isolated": isolated, "augment": augment}[sys.argv[1]]()
