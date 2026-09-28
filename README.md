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

Nothing the model says is ever forced into the file. Rewrites also pass a
conservative check for technical values in the original prose: paths, URLs,
addresses and digit-bearing tokens such as ports and versions must survive
unchanged. A failed check gets one retry, then keeps the original comment. This
is not a proof that the new wording is factually correct; facts expressed only
in ordinary prose still need human review.

## Style policy: humanizer-ru

What "reads well" means here is not this project's invention. The rules come from
[humanizer-ru](https://github.com/ilyautov/humanizer-ru), vendored at tag v3.30.0
under `vendor/humanizer-ru/` with its provenance in `MANIFEST.json` and its MIT
notice beside it. On that tag the repository carries exactly one skill, and this
is it: 18 files, byte-identical to the tag's tarball.

The rules are not restated in this project's prompt. Every request *loads* the
skill: pi is given `--skill vendor/humanizer-ru` and the prompt opens with
`/skill:humanizer-ru`, so what the model reads is the skill's own `SKILL.md` —
its modes, its marking step, its HARD BANS table, its fact lock, its verification
step. `src/commahum/skill.py` reads the invocation name out of the skill's
frontmatter rather than hardcoding it, and the request is refused before pi is
started if it does not open the way pi requires. Opening the way pi requires is
not obvious, and getting it wrong fails silently: see "The skill, and how pi
gets it" below.

Two files the flow reads around an edit travel with every request as
`--append-system-prompt`: `edit-log.md` (read before the work, rules from live
feedback, stronger than the catalogue's defaults) and `references/review.md`
(the brief for the cold re-read). The 67-pattern catalogue is 93 KB and the flow
opens it only when a pattern is unclear, so it is sent when `--catalog` asks for
it.

The bans are not only prose in a prompt. `src/commahum/bans.py` reads the
`HARD_BANS` table out of the vendored `markers.py` — through `ast`, so the
scanner's razdel and pymorphy3 stay out of the dependency graph — and the
rewriting refuses a body that *introduces* one. A comment that already said
«является» keeps it: the guard polices the rewrite, not the author. A refusal
costs the usual single retry, then the original comment stays and the run names
the ban it brought in.

## What is guaranteed, and what is not

"The skill is followed" can mean three different things, and they have different
answers. The middle column is what this project does about each.

| Claim | How it is held |
|---|---|
| the copy is the pinned revision | `MANIFEST.json` carries the sha256 of all 18 files taken from the tag; `tests/test_skill.py` recomputes them and fails on any difference |
| every request carries the rules | pi gets `--skill <path>`, discovery is off (`--no-skills`), and the prompt opens with the invocation read from the skill's frontmatter; a prompt that does not is refused before pi starts |
| the flow's own reading is present | `edit-log.md` and `references/review.md` are appended to every request; the catalogue with `--catalog` |
| the skill's machine rules are enforced | `bans.py` reads `HARD_BANS` from the vendored `markers.py`; a rewrite that introduces one is refused, retried once, reported |
| no rules, no run | a missing or unparseable skill stops the run with exit 2 and one sentence, before any file is read |
| the rules actually reach the model | `commahum probe` rigs a copy of the skill with tokens generated at run time and asks for them, plus one token written nowhere |
| the edit improved anything | measured, not asserted: `commahum report` runs the skill's own scanner over the before and after prose |

```sh
$ .venv/bin/commahum probe --model deepseek/deepseek-flash
rigged copy: /tmp/commahum-probe-tolx_rrc/humanizer-ru
  SKILL.md: ТЕЛО-12ca3c1a
  edit-log.md: ДОПОЛНЕНИЕ-f9188892
  asked for but written nowhere: ОТСУТСТВУЕТ-daaa53be

asking pi · deepseek/deepseek-flash · humanizer-ru

body and reading:
    КОНТРОЛЬНАЯ СТРОКА: ДОПОЛНЕНИЕ-f9188892 КОНТРОЛЬНАЯ СТРОКА: ТЕЛО-12ca3c1a
token that was never written:
    нет

  ok   the skill body reached the model
  ok   the flow reading reached the model
  ok   a token that exists nowhere was not invented

all 3 checks passed: the rigged files reached the model, and only they.
```

The probe rigs the material because two cheaper instruments lied. Asking the model
what it could see got "yes, and the version is v3.30.0" from a run with no skill at
all — the repository is public, so the version is recall, not evidence — and got
"no skill here" from a run where the skill was loaded. A token generated at run
time and appended to a copy of the skill cannot be recalled, so quoting it is
proof, and asking for a second token that was written nowhere keeps the first
answer from being mere compliance.

What is **not** guaranteed is that the model obeys: whether it really marked the
comments up before editing, how it weighed a pattern against a false positive, and
whether its cold re-read caught what it should have. Those are judgement, and no
test makes judgement certain. The mechanism is checked, the enforcement is
checked, the effect is measured, and the rest is visible in the diff a human is
meant to read.

## Measuring the edit

Removing what was already there is left to the model, so "did it work" is a
measurement rather than a claim. `commahum report` hands the before and after
prose to the skill's own scanner:

```sh
.venv/bin/commahum report --model deepseek/deepseek-flash file.nix
```

```text
ЧИСТОТА: было 17, стало 88/100  [чисто]
HARD BANS:
  ⛔ Комплексный подход/решение ×1 (стр. 2)
Ритм / типографика:
  ⚠ ровный ритм (CV=0.0, цель ≥0.45); ✓ тире чисто
Факт-замок (против before.txt):
  ✓ факт-замок цел: 21 фактов исходника перенесено, новых нет
```

The leftover ban in that run was in the original and the model kept it; the
guard deliberately does not police removal, because their own catalogue warns
that a term or a formal register can make a marker the right thing to write. The
scanner reports it, and that is the honest division: the pipeline enforces, the
scanner measures.

`report` answers the question for one file while you are reading its diff. The
question a change deserves — did the new prompt, the new model, `--catalog`, the
new skill revision make it *better* — needs the same corpus twice, so `measure`
runs the pipeline over a set of files, takes the scanner's numbers in its `--json`
mode, and compares them with a baseline:

```sh
$ .venv/bin/commahum measure --model deepseek/deepseek-flash --baseline eval/baseline.json eval/corpus
score_before: 59
score_after: 92
bans: 0
markers: 2
facts_kept: 16
facts_new: 0
facts_lost: 0

no regressions against eval/baseline.json
```

A lower score, a hard ban that appeared, a fact lost or invented: any of those is a
regression, for the corpus or for an individual file, and yields exit 1. Each
baseline record contains a file path, SHA256 of the original bytes and its own
scanner numbers, so a rename or edit cannot pass unnoticed when the file count
stays the same. Different files or a different model mean exit 2 (not comparable),
as does an older baseline without complete per-file numbers. `eval/` holds the
frozen corpus and its baseline; `--save` re-records it deliberately, not as a
reflex after a red run. Per-file grading costs one additional scanner invocation
per file; it does not make an additional model request.

## Authorizing a provider

Credentials are pi's, and this project keeps no copy of them. What a run needs to
know — will pi answer, and with what — is one command:

```sh
$ .venv/bin/commahum auth
deepseek: ready — the credential is in pi's store (/home/vathmiel/.pi/agent/auth.json, mode 0o600)
anthropic: ready — the credential comes from the environment
```

pi's own `auth check` answers `ready` or `not_ready` but never says *where* the
credential came from, and that is exactly the question when a run works on one
machine and not on another. So the source is worked out the way anything invisible
here is: ask with the environment as it is, then ask again with every provider
variable taken away. Ready in both means pi's store; ready only in the first means
the shell; not ready in either is not ready, and pi's own word for the reason is
printed. Without `--provider` every provider in the store is asked about, and
`--model provider/id` names one just as well.

Putting a key into pi's store is otherwise a TUI affair (`pi`, then `/login`).
`--store` does it from here, reading the key from stdin, a named variable or a file
— never from the command line, which the process table displays — and then *asking
pi* whether the key works:

```sh
$ .venv/bin/commahum auth --store deepseek --from-file ~/.hermes/.env --from-var DEEPSEEK_API_KEY
deepseek: the key is in /home/vathmiel/.pi/agent/auth.json, mode 0o600, and pi confirms it; 35 characters were never shown
```

The store is written atomically, every other provider is kept, and an entry pi does
not confirm is rolled back byte for byte — a credential is either confirmed by pi or
it is not a credential. A store file that is not valid JSON is refused rather than
overwritten, and a key is never printed, not even its first characters. A
subscription is not something this command can copy: `--login` hands the terminal to
pi, whose `/login` runs that flow and keeps refreshing its tokens afterwards.

## Status

Working end to end. 328 unit and packaging tests (stdlib `unittest`; the
isolated wheel install fetches dependencies from the package index), a clean
wheel/sdist install smoke, and a no-model invariant check:

```sh
.venv/bin/commahum check eval/corpus --quiet
# 5 files, 20 comments rewritten, 0 invariant violations
```

`commahum check` swaps the model for a stand-in (uppercase), splices, re-parses
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
- [x] the model call goes through pi, vendored in `vendor/pi/` in a checkout and
      `commahum/_vendor/pi/` in a wheel; the runner finds the bundled entry point
      and runs it with node
- [x] wheel and sdist carry both vendored trees; a wheel installed in a separate
      virtualenv reads its skill, ban table and pi from site-packages and passes
      `commahum check` without the source checkout
- [x] pi is asked hermetically — no tools, no session, no `AGENTS.md`, no skills,
      no extensions, no prompt templates — and the prose travels on stdin, never
      in the process table
- [x] providers, credentials and the default model belong to pi: `--provider`
      and `--model` are passed through as pi understands them, and a pi that is
      missing, refused or silent is reported in its own words
- [x] the style rules are the vendored humanizer-ru skill: every request loads it
      by path, opens with its invocation and carries the files its flow reads,
      while the instructions keep to the role, the genre and the reply protocol
- [x] the invocation name is read from the skill's frontmatter, and a request
      that does not open with it is refused before pi is started
- [x] the exact opening pi needs — the name followed by a space — is a property of
      the skill object, with the reason next to it, because pi finds no skill when
      that space is a newline and says nothing; discovery is off in these runs, so
      a skill installed elsewhere with the same name cannot shadow the pinned copy
- [x] no skill, no run: a missing or unparseable skill is exit 2, not a run with
      default rules
- [x] the pinned copy is checked offline against the sha256 of every file in the
      tag's tarball (`tests/test_skill.py`)
- [x] a rewrite that introduces a hard ban is refused, retried once, then
      reported — the ban table is read out of the vendored skill
- [x] a rewrite that loses, invents or changes a recognizable technical value
      (path, URL, address, number) is retried and otherwise kept unchanged;
      ordinary prose facts are not claimed to be verified
- [x] `--write` re-reads the file after the model request and skips it if its
      bytes changed or it vanished, rather than overwriting the newer version
- [x] compliance is measurable: the skill's own scanner grades before and after
      (`commahum report`), including the fact lock
- [x] the wiring itself is checkable against a live provider: `commahum probe`
      asks questions only the skill's files can answer
- [x] a change is judged by numbers: `commahum measure` records a frozen
      corpus by path and SHA256, checks scanner metrics per file and as a whole,
      and rejects regressions and incomparable baselines separately
- [x] what pi will answer with is one command: `commahum auth` names the provider,
      the credential's kind, and whether it came out of pi's store or the shell
- [x] the one thing pi does not say — where the credential came from — is computed
      by asking twice, the second time with every provider variable removed, rather
      than guessed from the presence of a variable
- [x] a key can be stored without the TUI, and only counts once pi itself confirms
      it: the write is atomic, keeps every other provider, and is rolled back byte
      for byte when pi does not confirm the entry
- [x] the key is never taken from the command line and never printed, not even a
      prefix — the command line is visible in the process table

## Usage

```sh
.venv/bin/commahum check /etc/nixos --quiet        # the invariant: no model, no network
.venv/bin/commahum path/to/file.nix               # show the diff, write nothing
.venv/bin/commahum --write path/or/dir            # apply it
.venv/bin/commahum report path/to/file.nix        # rewrite, then grade it with the scanner
.venv/bin/commahum measure --baseline eval/baseline.json eval/corpus   # is it still as good?
.venv/bin/commahum probe --model deepseek/deepseek-flash   # is the skill reaching the model?
.venv/bin/commahum model set deepseek/deepseek-flash # remember the model for commahum only
.venv/bin/commahum model                         # show the saved model
.venv/bin/commahum auth                            # what pi has configured, and from where?
.venv/bin/commahum auth --store deepseek --from-file ~/.hermes/.env --from-var DEEPSEEK_API_KEY
.venv/bin/commahum --model deepseek/deepseek-flash path/to/file.nix # override for one run
.venv/bin/commahum --catalog path/to/file.nix     # also send the 67-pattern catalogue
.venv/bin/commahum --quiet path/or/dir            # no progress, just the diff and the summary
```

A bare `commahum <paths>` means `rewrite`, because that is the safe default: read,
show, write nothing. The others are named because they are asked for by name —
`check` before trusting anything (it is the one command that needs no key and no
network), `report` when the question is "did that edit actually improve the text",
`measure` when the question is whether a *change* did, `probe` when the question
is whether the rules reach the model at all, `model` to remember which model
Commahum asks, and `auth` to see where pi found its credential. Only the
first word decides which command runs, so a file called `report` is `./report`.

Every request loads the vendored skill and carries the two files its flow reads;
`--catalog` adds the full pattern catalogue (93 KB, roughly 25k tokens per
request) for the cases where the flow's own advice is to open it.

Directories are walked for `*.nix`. The model call goes through pi — the copy of
it vendored in `vendor/pi/`, unless `$COMMAHUM_PI` points somewhere else.
`--model` is pi's own word for who answers (`deepseek/deepseek-flash`,
`openai-codex/gpt-5.5`, `sonnet:high`). `commahum model set provider/id` saves a
Commahum-only default in `~/.config/commahum/config.json` (or under
`$XDG_CONFIG_HOME` if set);
`commahum model` shows it, and `commahum model clear` removes it. An explicit
`--model` overrides the saved default for one run. Without either, pi picks its
own model. Use a provider-qualified model to select a provider: `--provider`
alone does not select its model, and conflicts with a saved model from a different
provider. None of this changes pi's global preferences. Credentials are pi's
business, and they live in pi's own store: `~/.pi/agent/auth.json` (mode 600)
holds one entry per provider — `{"deepseek": {"type": "api_key", "key": "..."}}` or the OAuth
triple a subscription signs in with — and a key in the environment is used when the
store has none. `commahum auth` says what pi is ready for and where the credential
came from; see "Authorizing a provider" below. Nothing about credentials is stored
in this project, and a run needs no environment variable when pi holds the
credential.

A run says where each request goes before it waits for it, so a directory that
takes minutes is not silent:

```sh
$ commahum --model deepseek/deepseek-flash /etc/nixos/hosts/deep-thought/generations.nix
hosts/deep-thought/generations.nix: asking pi · deepseek/deepseek-flash · humanizer-ru
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
second Ctrl+C leaves at once. Before replacing a file, `--write` compares its
current bytes with the bytes read before the model request: if they changed or
the file vanished, it skips that file, reports it and exits 1 after handling the
rest. This closes the long model-request window, not the tiny race between that
last read and `os.replace`; it is not an atomic compare-and-swap. A run that
cannot start at all — no node, no pi — is 2, with a sentence rather than a
traceback.

```sh
$ commahum --provider deepseek /etc/nixos/modules
modules/home/cli/fish.nix: asking pi · deepseek/deepseek-flash · humanizer-ru
^C
interrupted: SIGINT, stopped after 0 of 26 files, 0 written
0 files, 0 comments rewritten, 0 skipped, 0 kept, 0 requests
$ echo $?
130
```

## The model, through pi

This project does not carry a provider integration of its own. It asks
[pi](https://github.com/earendil-works/pi), which owns the provider catalogs, the
API keys, the OAuth grants for subscription plans and the retries. What stays
here is the request: the rules, the numbered prose, and the answer parsed back
into comments.

pi is vendored in `vendor/pi/` in a checkout — 0.87.1, MIT, `MANIFEST.json`
beside it with the provenance and the subtree hashes, the notice in
`LICENSE.pi`. Installed wheels carry the same bytes under
`commahum/_vendor/pi/`, alongside the installed humanizer-ru skill under
`commahum/_vendor/humanizer-ru/`. An sdist includes both trees and builds the
same wheel. Neither distribution depends on having this checkout on disk.
It is the published pi package minus the parts our path never runs: the source
maps, the 91 npm packages the bundle inlines, `docs/` and `examples/`. What
is left — `dist/`, the three packages the bundle imports at runtime,
`package.json` and `npm-shrinkwrap.json` — is 15.9 MB, byte-identical to the
package it came from.

Running the bundled pi needs node (>= 22.19.0, pi's own `engines`): the
runner spawns the `cli.js` in the vendored tree of this checkout or installed
package. The order is `$COMMAHUM_PI`, then that vendored copy, then a `pi`
from `PATH`; no separate pi installation is needed.

Inside a run pi is switched off in every way that matters:

```sh
--print               one request, answer on stdout, exit
--no-tools            no tools at all — the model cannot read or write anything
--no-session          nothing recorded
--no-context-files    no AGENTS.md or CLAUDE.md from the working directory
--no-skills --no-extensions --no-prompt-templates
```

`PI_SKIP_VERSION_CHECK` and `PI_TELEMETRY` keep it quiet. `PI_OFFLINE` is
deliberately *not* set: the model catalog and the credential refresh are pi's
business, and a stale token should be pi's problem to solve.

A vendored copy can drift if pi starts importing something on a path we do not
run. The failure is loud — node names the package it cannot find — and the fix is
the same copy step, with the hashes in `MANIFEST.json` updated; that file says
how. `tests/test_pi_runner.py` fails if the tree goes missing from a checkout.

This project once did the opposite: it vendored a provider layer and kept a
closure probe to stop the copy rotting, and it maintained an adapter of its own
for the Codex subscription. Both are gone, because pi already is that layer and
upstream maintains it. `git log` has them if the comparison is ever wanted.

## The skill, and how pi gets it

`vendor/humanizer-ru/` is a proper [Agent Skills](https://agentskills.io/specification)
directory: `SKILL.md` with `name`, `description` and `license` in its frontmatter,
`references/` and `scripts/` beside it. pi reads skills from `~/.pi/agent/skills/`,
`~/.agents/skills/`, `.pi/skills/` and `.agents/skills/`, and takes one directly
with `--skill`:

```sh
# one run, nothing installed — and the only form this project relies on
pi --skill /path/to/commahum/vendor/humanizer-ru

# every pi session on this machine, still a single source of truth
mkdir -p ~/.pi/agent/skills
ln -sfn /path/to/commahum/vendor/humanizer-ru ~/.pi/agent/skills/humanizer-ru
```

Skills load progressively: pi keeps a skill's `description` and pulls in the rest
when it is invoked, either as `/skill:humanizer-ru` at the start of a prompt or
from the model's own judgement. `--no-skills` turns discovery off while an
explicit `--skill` still loads, which is what keeps a hermetic run hermetic. The
invocation has to be part of the prompt text — a skill name passed as a separate
argument is not a slash command, and nothing loads.

How pi expands it, from its own code in `vendor/pi/dist/bundle/chunks/`:

```js
if (!text.startsWith("/skill:")) return text
spaceIndex = text.indexOf(" ")                 // the first space in the whole prompt
skillName  = text.slice(7, spaceIndex)         // the name ends there, not at a newline
skill      = skills.find(s => s.name === skillName)
if (!skill) return text                        // untouched: no skill, no warning
return `<skill name=…>\n…\n</skill>\n\n` + args
```

Two consequences, both learned here the hard way. The name ends at a *space*: a
prompt that opens with `/skill:humanizer-ru` and a newline hands pi a name like
`humanizer-ru\nКомментарии:`, nothing matches, and the prompt goes out unexpanded
— the run continues with no rules and nothing to indicate it. And the expansion is
silent, so this cannot be noticed by watching the exit status; it has to be poured
into the prompt shape. `Skill.opening` is that shape (`/skill:humanizer-ru `, with
the space), `pi_runner` refuses anything else before starting pi, and
`commahum probe` proves the body arrives.

Checked on this host with a rigged copy of the skill: before the opening was
fixed, 0 of 6 runs could quote a token only that file contained; after, 6 of 6
could, and 3 of 3 obeyed a rule written into the same file.

This project relies on the first form — `--skill`, explicit, no installation —
and hands pi the skill by absolute path on every request. In a checkout that
path is `vendor/humanizer-ru/`; in a wheel it is the copy shipped inside the
installed package. The symlink is for a human's own pi sessions on a machine
where this checkout lives; a run of `commahum` does not need it and does not
read anything from the home directory. With discovery off, a skill installed
under the same name elsewhere cannot shadow the copy that came with this
installation.

## Layout

```
src/commahum/comments.py      comment discovery: Tree-sitter → byte ranges
src/commahum/selection.py     which comments are prose, and which parts survive verbatim
src/commahum/rewrite.py       the round trip: build, parse, validate, retry
src/commahum/patch.py         byte-range splice + the refusal rules
src/commahum/diff.py          unified diff of original vs rewritten
src/commahum/pi_runner.py     the model call: run pi, prose in, text out
src/commahum/bans.py          the hard-ban guard, reading the vendored rule table
src/commahum/skill.py         where the skill is, what it is called, what the flow reads
src/commahum/asking.py        the options every command shares, and the one generator
src/commahum/paths.py         arguments in, files out
src/commahum/interrupts.py    stopping a run between files
src/commahum/cli.py           the command line: six commands, one per question
src/commahum/auth.py          `auth`: what pi is ready for, where the credential came from
src/commahum/report.py        `report`: before/after grading with the vendored scanner
src/commahum/measure.py       `measure`: the corpus, the numbers, the baseline
src/commahum/probe.py         `probe`: is the skill really reaching the model?
src/commahum/selfcheck.py     `check`: the invariant, with a stand-in model
eval/                         the frozen corpus and the recorded baseline
tests/                        unit tests (stdlib unittest)
vendor/pi/                    pi itself, pinned (see "The model, through pi")
vendor/humanizer-ru/          the style rules (see "Style policy: humanizer-ru")
LICENSE                       MIT, for this project's own code
spikes/001-codex-vendor/      record of the removed Codex adapter (historical)
```

## License

This project's own code is MIT — see [LICENSE](LICENSE). What it carries keeps its
own terms, and each notice travels with the copy: pi is MIT (Mario Zechner,
`vendor/pi/LICENSE.pi`), the humanizer-ru skill is MIT (`vendor/humanizer-ru/
LICENSE.humanizer-ru`), and the npm packages inlined inside the vendored pi keep
theirs under `vendor/pi/node_modules/`. An earlier revision vendored the Hermes
agent layer as well; that copy carried its MIT license (`spikes/001-codex-vendor/
LICENSE.hermes`) and is no longer in the tree, though it stays in the history.

## Development

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[style]' hatchling
.venv/bin/python -m unittest discover -s tests -t . -v
```

`hatchling` builds the wheel and sdist in `tests/test_packaging.py`; its isolated
install test downloads runtime dependencies into a fresh virtualenv. Node is
needed for the bundled pi smoke test. `[style]` is only for `commahum report`
and `commahum measure`, which grade prose with the vendored scanner. A plain
rewrite needs only the parser and node for the bundled pi.
