# commahum

Stylistic rewriting of comments in Nix sources, with a hard guarantee: **every
byte that is not a comment survives unchanged.**

## Why it works that way

A language model is good at rephrasing a sentence and bad at being trusted with
a source file. So it never sees the file. It sees comment prose, and its output
is spliced back into the original bytes at ranges the parser identified.

```
source file (bytes)
      │
      ▼
  Tree-sitter ──── parses bytes, not text; ranges are byte offsets
      │
      ├── everything that is not a comment ──────────┐ unchanged
      │                                              │
      └── comment ranges                             │
               │                                     │
               ▼                                     │
       selection: prose only,                        │
       markers split off                            │
               │                                     │
               ▼                                     │
       one request per file:                        │
       numbered prose in, numbered prose out        │
               │                                     │
               ▼                                     │
        byte-range splice ──────────────────────────►┘
               │
               ▼
        diff (what you review)
```

Because the splice is an explicit range replacement rather than a search and
replace, a rewrite can never land somewhere the parser did not point at. A range
that is not a real comment is refused, not repaired.

## What the model is allowed to see

Only prose. Before a request is built, each comment is split into three parts:

- a **prefix** that must survive verbatim — the `#` run, the space after it, and
  a `TODO:`/`FIXME:`/`HACK:`/`XXX:` marker if there is one,
- the **body**, which is the prose — this is all the model receives,
- a **suffix** for block comments — the ` */`.

The model never receives code, file names, comment markers or delimiters, and it
never receives one comment's neighbours. Comments that are not prose are not
sent at all: `# nix-shell -p ...`, `shellcheck` and modeline directives, SPDX
tags, comments made only of URLs or paths, separator lines, and the shebang.

## What happens to the answer

1. Sections are matched back by number; a missing number is a failure, not a
   guess.
2. The body is reassembled with the preserved prefix and suffix.
3. The patcher checks the result: same comment style, same `#` marker, no
   newline inside a line comment, and — by re-parsing the fragment — that it is
   still exactly one comment.
4. Anything that fails is retried once on its own. If it fails again the comment
   keeps its original text and the run reports it.

Nothing the model says is ever forced into the file.

## Status

Working end to end. 115 unit tests (stdlib `unittest`, no network) plus an
invariant check over a real config tree:

```sh
.venv/bin/python examples/selfcheck.py /etc/nixos --quiet
# 39 files, 1131 comments rewritten, 0 invariant violations
```

`selfcheck.py` swaps the model for a stand-in (uppercase), splices, re-parses
the result and compares every non-comment byte run — the guarantee, checked by
the parser rather than asserted.

- [x] comment extraction: byte ranges for `#` and `/* */`, ignoring
      comment-looking text inside strings and interpolation
- [x] selection: prose only, with `#` runs and `TODO:`-style markers split off
- [x] byte-range splice, refusing ranges that are not comments, overlapping
      rewrites, style changes, dropped markers, newlines in line comments,
      shebang rewrites, and text that would end the comment early
- [x] diff rendering, lossless for files that are not valid UTF-8
- [x] one request per file, numbered in and out, one retry per failed comment
- [x] CLI, showing the diff by default and writing only with `--write`
- [x] the CLI names the request target and reports progress per file, with
      `--quiet` dropping both
- [x] interrupts — Ctrl+C, `SIGTERM`, `SIGHUP`, a reader that closed the pipe —
      stop the run between files, name the signal in the exit status, and leave
      neither a half-written file nor a temporary file behind
- [x] the vendored provider layer imports and resolves providers on its own,
      guarded by `tests/test_vendor_tree.py`
- [x] any of its providers can drive the rewriting: `--provider deepseek` asks
      DeepSeek's own endpoint through that layer

## Usage

```sh
.venv/bin/commahum path/to/file.nix       # show the diff, write nothing
.venv/bin/commahum --write path/or/dir    # apply it
.venv/bin/commahum --provider deepseek path/to/file.nix   # ask another provider
.venv/bin/commahum --quiet path/or/dir    # no progress, just the diff and the summary
```

Directories are walked for `*.nix`. The model call goes through the Codex
subscription adapter ported in `src/commahum/codex_adapter.py` (provenance in
`spikes/001-codex-vendor/`); `--model` overrides the model. With `--provider`,
the call goes through the vendored Hermes layer instead — the adapter is not
involved at all then.

A run says where each request goes before it waits for it, so a directory that
takes minutes is not silent:

```sh
$ commahum --provider deepseek /etc/nixos/hosts/deep-thought/generations.nix
hosts/deep-thought/generations.nix: asking deepseek · deepseek-flash
hosts/deep-thought/generations.nix: 16 rewritten, 1 skipped, 0 kept
1 files, 16 comments rewritten, 1 skipped, 0 kept, 1 requests
--- a/hosts/deep-thought/generations.nix
+++ b/hosts/deep-thought/generations.nix
@@ -1,5 +1,5 @@
-# Два ограничения поколений этого хоста остаются рядом: предел для ESP
+# Два ограничения поколений этого хоста стоят рядом: предел для ESP
```

Progress and the summary go to stderr, the diff to stdout, so redirecting stdout
still yields nothing but the patch.

Stopping is safe. Ctrl+C, a termination signal or a closed stdout ends the run
between files: every file is either rewritten whole or left as it was — the new
bytes go to a temporary file and are moved into place with one `os.replace` —
the summary says how far the run got, and the exit status names the signal
(130 for Ctrl+C, 143 for `SIGTERM`, 129 for `SIGHUP`, 141 for a closed pipe). A
second Ctrl+C leaves at once.

```sh
$ commahum --provider deepseek /etc/nixos/modules
modules/home/cli/fish.nix: asking deepseek · deepseek-flash
^C
interrupted: SIGINT, stopped after 0 of 26 files, 0 written
0 files, 0 comments rewritten, 0 skipped, 0 kept, 0 requests
$ echo $?
130
```

## Provider integrations

The rewriting path talks to the Codex subscription through this project's own
adapter. Anything beyond that comes from `vendor/hermes/`: a copy of Hermes'
provider layer — the per-provider sign-in modules, the provider registry, the
model catalogs and the credential store — so another integration (OpenRouter,
Nous, Qwen, xAI, MiniMax, Z.AI/Kimi, device flow, OAuth grants) can be added
without hand-vendoring it again.

It is a copy, not an install, so it can drift: an integration added later may
import a module nobody copied. `tools/closure_probe.py` finds and repairs that.

```sh
.venv/bin/python tools/closure_probe.py integrations
.venv/bin/python tools/closure_probe.py capture --provider openrouter
.venv/bin/python tools/closure_probe.py augment --provider openrouter
```

`integrations` imports every entry point from the tree and needs no network —
it takes a few seconds and is the check to run after touching the tree.
`capture` runs one real request and copies the modules it used; `augment` repeats
that, copying the next missing module each round until the call closes. Both of
those need the installed Hermes to copy from, so run them with its interpreter.
The `isolated` mode is the same call with the vendored tree on `sys.path` and a
finder that refuses to load a vendored name from anywhere else.

Import-time dependencies are the `hermes` extra in `pyproject.toml`. A
provider's own client library is not there on purpose: add it when you add the
provider.

Each provider is more than its modules: `vendor/hermes/plugins/model-providers/
<name>/` holds its profile — base URL, aliases, capabilities, which model suits
an auxiliary task. `providers/__init__.py` *scans* that directory instead of
importing it, so an absent profile raises nothing: the provider keeps importing
and quietly loses its default model. That is exactly how the DeepSeek path looked
finished while sending an empty model name; `capture` now copies the profiles too
and `tests/test_vendor_tree.py` fails if they are missing.

Provenance travels with the code: `vendor/hermes/MANIFEST.json` pins the upstream
revision, `vendor/hermes/LICENSE.hermes` carries the MIT notice, and
`vendor/hermes/captured.json` is the tree's module inventory.

## Layout

```
src/commahum/comments.py      comment discovery: Tree-sitter → byte ranges
src/commahum/selection.py     which comments are prose, and which parts survive verbatim
src/commahum/rewrite.py       the round trip: build, parse, validate, retry
src/commahum/patch.py         byte-range splice + the refusal rules
src/commahum/diff.py          unified diff of original vs rewritten
src/commahum/codex_adapter.py Codex subscription request + OAuth token rotation
src/commahum/hermes_provider.py prompt -> text through the vendored provider layer
src/commahum/hermes_tree.py   where that tree lives, and loading it fail-closed
src/commahum/cli.py           the command line
examples/selfcheck.py         invariant check over any Nix tree
examples/smoke_codex.py       live request smoke test for the adapter
tests/                        unit tests (stdlib unittest)
vendor/hermes/                Hermes provider layer (see "Provider integrations")
tools/closure_probe.py        closure check and repair for the vendored tree
spikes/001-codex-vendor/      provenance for the adapter (manifest, MIT notice)
```

## Development

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[codex,hermes]'
.venv/bin/python -m unittest discover -s tests -t . -v
```
