# Scripts module contract

Cross-module rules live in [`../CODEX.md`](../CODEX.md). This directory owns
operator orchestration only; scientific contracts, API behavior, storage
queries, and UI state machines stay in their authoritative modules.

- `verify-all.sh` is the single offline release gate. It may create only
  temporary test/migration state and must never call MinerU, GPT, or a
  deployed DeepShovel instance.
- `acceptance-batch.mjs` is an explicit online operator tool. It accepts one or
  more ordered `--source-dir` values, validates their combined PDF inventory
  against `--expected-count` (default `14`), talks only to the Web API, never
  bypasses the Compiler boundary, records bounded metadata without
  document/model content, and never resets or deletes a corpus.
- Build scripts synchronize vendored runtime assets and validate the produced
  artifact. Deployment scripts replace the application subtree while keeping
  data, secrets, logs, and Tunnel credentials outside it.
- Keep retries bounded and scoped to operations known to be replay-safe. Never
  make a destructive reset or a live model/parser call part of an ordinary
  build, test, update, or health command.
- Secrets are read from the owner-authorized private-repository environment
  files or the installed secrets directory; scripts must not print them or copy
  them into browser build artifacts.
