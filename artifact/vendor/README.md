# Public release license and scope

This recorded source excerpt is now distributed under the repository AGPL-3.0-only license. The full current application is at the repository root. The source hashes below refer to the earlier recorded excerpt. Historical local paths and execution receipts are not prerequisites for the public reproduction commands in the root README.

# Recorded production source excerpt

This internal submission copy contains exact source bytes from the owner's DeepShovel compiler. It makes no open-source licensing assertion and does not publish the private repository. `source-manifest.json` records every file's SHA256 and size. No environment files, credentials, databases, PDFs, frontend or model weights are included.

AST relative-import discovery was narrowed to the union of actual imported production modules in the two offline replays: **35 Python modules plus one ontology JSON, 1,086,671 source/data bytes**. This is a deliberately incomplete application excerpt. Optional server, Qdrant and remote-model branches are not runnable application features in this bundle. The algorithms invoked by the fixture remain byte-identical to production.

## Install and run

From the artifact root, use Python 3.12 (tested 3.12.13). The four direct public dependencies are pinned to the versions actually installed in the verification environment. Pip resolves their normal transitive dependencies.

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r vendor/requirements.txt
.venv/bin/python replays/run_all.py
```

The final line is the single replay command. It runs both fixtures with only the bundled compiler path selected. Each creates a fresh timestamp/SHA output directory under its replay directory. To replay one fixture independently, `python replays/scope_contract/run.py` and `python replays/admission_contract/run.py` default to the bundled vendor structure; explicit `--repo` still supports another checkout, and `--out` requires a new directory.

Actual verification used the pre-existing project venv (not a fresh installation), with PYTHONPATH set only to this bundled compiler and no checkout override. Results are frozen at `../qa/vendor-final-scope/` and `../qa/vendor-final-admission/`: **18/18 channel checks; baseline accepted and 6/6 negative controls rejected**. The first imports 23 production modules and the second 24; each `module-files.json` records actual `module.__file__` paths. Runtime assertions reject any production module imported from outside the selected vendor directory. No service was started, no model or network was called, and no key was read by the fixtures. A subsequent genuinely fresh Python 3.12.13 venv was created outside the submission artifact at `revision/clean-replay-env`, without project site-packages. Installing exactly these four pinned direct requirements succeeded (pip exit 0); their normal public transitive dependencies were resolved by pip. `python replays/run_all.py --out qa/clean-replay` then succeeded (exit 0): 18/18 scope channel checks, valid admission control accepted and 6/6 negative controls rejected. Runtime assertions verified all production module paths are vendored, and the interpreter path excluded the original project venv site-packages. See `../qa/clean-install.log`, `../qa/clean-replay.log`, `../qa/clean-install-verification.json`, and each fixture’s `module-files.json`. The verification JSON records every installed package version, interpreter/prefix/site isolation, and both summaries. The clean environment itself is intentionally not included in the artifact.

The two runners and local fixtures are adapted copies; earlier evidence-directory runners and historical outputs remain untouched. This source excerpt only supports the limited contract tests described in those artifacts, not a scientific benchmark or full deployed-system reproduction.
