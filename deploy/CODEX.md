# Local public deployment

Follow the root [engineering contract](../CODEX.md) and the normative
`publish-local-mac-app` architecture.

`local-public.json` is the only tracked source for non-secret deployment
identity, ports, URLs, runtime paths, service labels, health paths, and public
deny rules. Scripts may derive generated Wrangler, Cloudflare, plist, and Swift
values from it; they must not maintain independent copies.

The installed runtime lives below `~/Library/Application Support/DeepShovel/`.
Only its `app/` subtree is replaceable. `data/`, `logs/`, `secrets/`, and
`cloudflared/` are persistent and must never be targets of `rsync --delete`.
First installation may copy the existing ignored local D1/R2 and Compiler job
stores; update must never recopy or delete them.

An update activates `app/` and `venv/` as one filesystem transaction. Any
error, interrupt, or termination before local health succeeds must stop the
replacement, restore both prior directories, and restart the service only when
it was running before the update. After local health succeeds, the replacement
is committed and interruption must leave its working directory intact.
Rollback cleanup must first move both fixed rollback names to unique inert
discard tombs in one signal-masked same-filesystem section. Cleanup may leave
an inert discard after interruption, but it must never leave a fixed rollback
name that blocks the next update. Every individual restore failure must propagate;
never mark a partial app/venv restore successful.
Displaced replacement cleanup is best-effort and begins only after both app and
venv are restored. A cleanup failure must not interrupt that two-component
rollback. Once rollback begins, additional INT/TERM/HUP signals remain ignored
until both components are restored so a second signal cannot create a mixed
runtime.

D1 migrations commit before runtime health is known and are not rolled back with
`app/` and `venv/`. Every migration must therefore be expand-only or otherwise
backward-compatible with the immediately previous runtime. A destructive or
incompatible migration requires a separately designed data backup/restore
release; runtime rollback alone is not sufficient.

The Web Worker and Compiler form one supervised application service group. Web
binds `127.0.0.1:8100`; Compiler remains fixed to `127.0.0.1:8011`; Cloudflare
Tunnel is the only public ingress. Never route Compiler, logs, generated files,
or operator controls through the Tunnel.

The supervisor recovers an exited component independently. For a live process,
each health probe allows five seconds and only continuous failures lasting at
least 120 seconds trigger a restart; one success resets that window. Preserve
the Web's 90-second and Compiler's 240-second startup grace periods. Compiler
`/health` is the liveness signal; `/ready` gates deployment and model warmup,
not automatic restarts. Brief event-loop stalls must not terminate active work.
Probe diagnostics contain only curl exit code, HTTP status and elapsed time.

The current hostname intentionally follows Huishu's public-access model and
does not enable Cloudflare Access. A named Tunnel is transport, not
authorization. If the corpus later becomes private, enable Access or application
authentication as a separate change; do not imply that Tunnel itself authenticates users.

Generated files and credentials are untracked. Do not print or copy Tunnel
credential contents, GPT keys, or the Compiler shared secret.

`compile-sync` is a separate namespaced LaunchAgent: the background work pump. It calls
`POST /api/internal/work` on the loopback Web with the shared credential from
`secrets/web.env`, one bounded pass at a time, faster while work remains. All work state
lives in D1; the pump reads no database files and never calls the Compiler. Its status is
`data/compile-sync/status.json` (counters only). Because the pump requires the matching
Web route, install releases that change it with `make public-update`;
`make public-sync-update` is only for later pump-only fixes.
