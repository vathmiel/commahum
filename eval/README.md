# The frozen corpus

Five small files, each one put there to make a different part of the flow fail
loudly if it breaks. They are frozen: a baseline records each path and the SHA256
of its original bytes, not just a file count, and `commahum measure` refuses to
compare a different corpus even when it contains the same number of files.

| File | What it exercises |
|---|---|
| `cancelerit.nix` | канцелярит in the wild: отглагольные существительные, «производится», «осуществляется». The score before should be low and the rewrite should move it up |
| `facts.nix` | the fact lock: addresses, ports, paths, counts, dates, a URL, a default that is being overridden. A rewrite that drops or invents any of them is a regression, and the scanner says which |
| `already-good.nix` | comments that already read fine. The skill's own advice is to return them unchanged, so a low `applied` count here is a good sign, not a bad one |
| `skipped.nix` | what the selection must not touch: `shellcheck` and `nix-shell` directives, a bare URL, and `TODO:`/`FIXME:` markers whose bodies may change while the marker survives |
| `english.nix` | an English comment. The rules are Russian, so the honest outcome is that it is left alone; if it comes back rewritten, that is a finding |

Replies returned verbatim are counted as `unchanged`; only actual edits count
as `applied`. New measurements record `rewrite_counts_version: 2`. The historical
checked-in baseline predates that distinction, so it must be re-recorded
deliberately before comparing it with a new run; its counts cannot be migrated
without the original model replies.

## Running it

```sh
.venv/bin/commahum measure --model deepseek/deepseek-flash eval/corpus
.venv/bin/commahum measure --model deepseek/deepseek-flash --baseline eval/baseline.json eval/corpus
```

No key needs to be exported: pi reads it from its own store
(`~/.pi/agent/auth.json`), which is why the baseline records the model but no
credential. `commahum auth` says whether pi is ready and where the credential came
from; `commahum auth --store <provider>` puts one there without pi's TUI.

The first prints the numbers; the second compares them with the recorded baseline
and exits 1 on a regression — a lower score, a hard ban that appeared, a fact that
was lost or invented, in the whole corpus or any one file — and 2 when the two
runs are not comparable (different paths or hashes, different model, or an old
baseline without complete per-file results). Per-file results take one additional
scanner run per file, but no additional requests to the model.

`--save eval/baseline.json` re-records it. Do that only when the change was meant to
move the numbers, and say why in the commit message: a baseline that is silently
refreshed measures nothing.

`eval/baseline.json` records the model it was taken with. Measuring the same corpus
through a different model is a different experiment, and the command refuses to
pretend otherwise — compare the two by taking two baselines, not by overwriting one.

Nothing here is written to: `measure` reads the corpus and writes its own temporary
files. The `prose` that the scanner grades is the same text the model was sent,
markers stripped — see `src/commahum/selection.py`.
