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
regression, and so is exit 1. A baseline taken over a different number of files or
against a different model is not comparable, and saying that is exit 2 rather than a
quiet pass. `eval/` holds the frozen corpus and the recorded baseline; `--save`
re-records it, which should be a deliberate act with a reason, not a reflex after a
red run.

## Status

Working end to end. 202 unit tests (stdlib `unittest`, no network) plus an
invariant check over a real config tree and a live probe of the skill wiring:

```sh
.venv/bin/commahum check /etc/nixos --quiet
# 39 files, 1131 comments rewritten, 0 invariant violations
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
- [x] the model call goes through pi, vendored in `vendor/pi/`, so a checkout
      needs nothing installed beyond node: the runner spawns
      `node vendor/pi/dist/bundle/cli.js`
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
- [x] compliance is measurable: the skill's own scanner grades before and after
      (`commahum report`), including the fact lock
- [x] the wiring itself is checkable against a live provider: `commahum probe`
      asks questions only the skill's files can answer
- [x] a change is judged by numbers: `commahum measure` runs a frozen corpus,
      records the metrics as a baseline and calls a lower score, a new ban or a
      moved fact a regression — two runs of the same corpus agreed exactly

## Usage

```sh
.venv/bin/commahum check /etc/nixos --quiet        # the invariant: no model, no network
.venv/bin/commahum path/to/file.nix               # show the diff, write nothing
.venv/bin/commahum --write path/or/dir            # apply it
.venv/bin/commahum report path/to/file.nix        # rewrite, then grade it with the scanner
.venv/bin/commahum measure --baseline eval/baseline.json eval/corpus   # is it still as good?
.venv/bin/commahum probe --model deepseek/deepseek-flash   # is the skill reaching the model?
.venv/bin/commahum --provider deepseek path/to/file.nix    # ask another provider
.venv/bin/commahum --catalog path/to/file.nix     # also send the 67-pattern catalogue
.venv/bin/commahum --quiet path/or/dir            # no progress, just the diff and the summary
```

A bare `commahum <paths>` means `rewrite`, because that is the safe default: read,
show, write nothing. The others are named because they are asked for by name —
`check` before trusting anything (it is the one command that needs no key and no
network), `report` when the question is "did that edit actually improve the text",
`measure` when the question is whether a *change* did, and `probe` when the question
is whether the rules reach the model at all. Only the first word decides which
command runs, so a file called `report` is `./report`.

Every request loads the vendored skill and carries the two files its flow reads;
`--catalog` adds the full pattern catalogue (93 KB, roughly 25k tokens per
request) for the cases where the flow's own advice is to open it.

Directories are walked for `*.nix`. The model call goes through pi — the copy of
it vendored in `vendor/pi/`, unless `$COMMAHUM_PI` points somewhere else.
`--model` is pi's own word for who answers (`deepseek/deepseek-flash`,
`openai-codex/gpt-5.5`, `sonnet:high`), and naming the provider there is what
picks it: `--provider deepseek` on its own still gets an answer from the model pi
is configured with, which is why the progress line says so. Credentials are pi's
too: a key is read from the environment (`DEEPSEEK_API_KEY`), a subscription plan
is signed in through pi itself (`pi` in a terminal, `/login`, then
`pi auth check --provider openai-codex` to see where it stands). Nothing about
credentials is stored in this project.

A run says where each request goes before it waits for it, so a directory that
takes minutes is not silent:

```sh
$ commahum --provider deepseek /etc/nixos/hosts/deep-thought/generations.nix
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
second Ctrl+C leaves at once. A run that cannot start at all — no node, no pi —
is 2, with a sentence rather than a traceback.

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

pi is vendored in `vendor/pi/` — 0.87.1, MIT, `MANIFEST.json` beside it with the
provenance and the subtree hashes, the notice in `LICENSE.pi`. It is the
published package minus the parts our path never runs: the source maps, the 91
npm packages the bundle inlines, `docs/` and `examples/`. What is left — `dist/`,
the three packages the bundle imports at runtime, `package.json` and
`npm-shrinkwrap.json` — is 15.9 MB, byte-identical to the package it came from.

Running it needs only node (>= 22.19.0, pi's own `engines`), because the runner
spawns `node vendor/pi/dist/bundle/cli.js`. The order is `$COMMAHUM_PI`, then the
vendored copy, then a `pi` from `PATH`: a checkout works with nothing installed,
and a host or nix-store installation takes over with one variable.

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
and hands pi the skill by absolute path on every request. The symlink is for a
human's own pi sessions on a machine where this checkout lives; a run of
`commahum` does not need it and does not read anything from the home directory.
With discovery off, a skill installed under the same name elsewhere cannot shadow
the pinned copy: what a run loads is the one this checkout carries.

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
src/commahum/cli.py           the command line: rewrite, report, probe, check
src/commahum/report.py        `report`: before/after grading with the vendored scanner
src/commahum/measure.py       `measure`: the corpus, the numbers, the baseline
src/commahum/probe.py         `probe`: is the skill really reaching the model?
src/commahum/selfcheck.py     `check`: the invariant, with a stand-in model
eval/                         the frozen corpus and the recorded baseline
tests/                        unit tests (stdlib unittest)
vendor/pi/                    pi itself, pinned (see "The model, through pi")
vendor/humanizer-ru/          the style rules (see "Style policy: humanizer-ru")
spikes/001-codex-vendor/      record of the removed Codex adapter (historical)
```

## Development

```sh
python -m venv .venv
.venv/bin/python -m pip install -e '.[style]'
.venv/bin/python -m unittest discover -s tests -t . -v
```

`[style]` is only for `commahum report` and `commahum measure`, which grade prose
with the vendored scanner. The rewriting path needs nothing beyond the parser: the model
is reached by running the vendored pi with node.
