# DeepShovel on a local Mac with a public URL

This bundle keeps the Mac as the server. DeepShovel Web and Compiler run only
on loopback, a named Cloudflare Tunnel provides outbound-only ingress, and a
native menu-bar app monitors the service group, local Web health, Tunnel agent,
and public health independently.

## One-time configuration

1. The deployment reuses the existing Cloudflare-managed `iiot-shxt.xyz` zone
   and publishes `deepshovel.iiot-shxt.xyz`. The hostname is fixed once in
   [`local-public.json`](local-public.json); the installer never substitutes a
   temporary URL or modifies the existing `huishu-data` Tunnel.
2. This installation intentionally matches the existing Huishu public-access
   model and does not enable Cloudflare Access. Tunnel capacity and the number of
   applications are unrelated to Zero Trust plans. If the corpus later becomes
   private, enable Access or add application authentication as a separate change.
3. Ensure ignored root `.dev.vars` contains `COMPILER_SHARED_SECRET` and ignored
   `compiler/.env.local` contains the fixed `GPT_API_KEY`. Stop the current
   `npm run dev` and `npm run dev:compiler` processes before first installation
   so SQLite/R2 state can be copied consistently.

Then run one command from the repository:

```bash
make public-install
```

The first run reuses or creates the named `deepshovel` Tunnel, creates the exact
DNS route without overwriting an unrelated record, imports the existing local
D1/R2 and Compiler data once, installs five current-user LaunchAgents, compiles
the menu-bar app, waits for local and public health, and opens the public URL.
Cloudflare may open a browser for one-time account authorization.

The installer never substitutes a Quick Tunnel or `trycloudflare.com` URL.

## Daily operation

```bash
make public-status   # process + local HTTP + Compiler + Tunnel + public HTTP
make public-open
make public-restart
make public-logs
```

Start and restart wait for Web and Compiler retrieval warmup, then verify public
health before reporting success. Startup validates and reuses existing ranking
features; only missing or stale projections are rebuilt. Compiler supervision uses
the fast local `/ready` endpoint, which never contacts remote MinerU; `/health`
remains a separate dependency diagnostic. The controller shows Compiler as
starting during the initial 240-second window, stopped when its service group is
stopped, and abnormal after a timeout or loss of readiness. It does not report
readiness until local initialization and model warmup finish.
Lifecycle commands share a process lock: overlapping operations
are rejected, and repeating start after completion reuses the running services.
Stop waits for both local ports and the vector-store lock to be released before
allowing another start. Shutdown also terminates workers whose parent exits first.
No lock files or persistent data are deleted to recover a service.

When fake-IP DNS is active, the Tunnel resolves real Cloudflare edge addresses.
It uses the physical interface only if that interface can reach an edge; otherwise
it follows the current system route, without changing global proxy settings.

After editing the repository, deploy an update without Cloudflare login or DNS
changes:

```bash
make public-update
```

The update builds and tests the source, stages a new runtime, preserves all
data/secrets/logs/Tunnel credentials, restarts only the application service
group, verifies both health endpoints and one hashed JavaScript asset through
the local and public URLs, and refreshes the menu-bar binary. The asset probe is
intentional: an API can remain healthy while a misrouted static bundle leaves
the browser unable to start.

The compact hexagonal network icon in the macOS menu bar opens the native
**DeepShovel 控制台**. It reports Web, Compiler, named Tunnel, public URL,
LaunchAgent, and keep-awake health separately; its color summarizes the overall state.
The panel also provides start/restart, stop, refresh, website, logs, and service
folder actions. It checks every seven seconds and continues running after the
panel is closed. If macOS has hidden menu-bar items because the bar is crowded,
temporarily close another menu-bar app or adjust the system menu-bar layout; the
DeepShovel icon uses the minimum square width to reduce that risk.

Only the menu controller starts automatically at login. Business services and
the separate `caffeinate -i -s` agent remain stopped until an explicit start.
Start enables idle-sleep prevention, stop releases it, and restart retains it
through the transition. Display sleep remains allowed. Generated LaunchAgents
preserve this manual-service policy during future updates.
Web and Compiler have independent
health supervision with bounded exponential restart backoff, so one transient
failure does not restart the other component. A separate recovery agent retries
public health and restarts only the Tunnel after three consecutive public
failures while local Web remains healthy. All five project agents start again
at macOS login and launchd restarts an agent if its process exits.
The controller holds a process lock under the installation data directory, so
manually opening the app while its LaunchAgent is running cannot create a second
menu-bar item.

## Installed locations

- Runtime: `~/Library/Application Support/DeepShovel/app/`
- Persistent D1/R2 and Compiler data:
  `~/Library/Application Support/DeepShovel/data/`
- Private environment files:
  `~/Library/Application Support/DeepShovel/secrets/`
- Stable logs: `~/Library/Application Support/DeepShovel/logs/`
- Tunnel config: `~/Library/Application Support/DeepShovel/cloudflared/`
- Menu bar: `~/Applications/DeepShovel Control.app`
- LaunchAgents: `~/Library/LaunchAgents/xyz.iiot-shxt.deepshovel.*.plist`
- Shared collision registry:
  `~/Library/Application Support/Local Public Apps/registry.json`

After first installation, update the deployed GPT key or shared secret only
in the installed `secrets/` files, then run `make public-restart`. Repository
environment files are intentionally not recopied during updates.

## Security and availability

Only Web port `8100` is routed. Compiler `8011`, local data, logs, control
scripts, and generated files are never Tunnel ingress targets. The Web health
response is deliberately small and public; Compiler health remains loopback-only.

Cloudflare Tunnel supplies encrypted transport, not authorization. The current
hostname is public and unauthenticated, matching the requested Huishu deployment
model. The Mac must remain awake, online, and logged into this
user account; user LaunchAgents are unavailable while the Mac is shut down,
asleep, logged out, or blocked by network policy.

临时 AutoDL 解析切换：实验室 MinerU 始终是默认配置。临时隧道已就绪时，执行
`python3 scripts/set-mineru-profile.py autodl`，读取本机既有凭据，仅检查健康状态后将
运行目录 `secrets/compiler.env` 的 MinerU 地址、令牌和并发设为临时端点、既有令牌和 4；
`python3 scripts/set-mineru-profile.py status` 只显示地址、并发和令牌是否存在；
`python3 scripts/set-mineru-profile.py parse` 先检查公网解析服务
`https://parse.iiot-shxt.xyz` 的健康状态，再改用其异步任务接口（提交、轮询、取结果，
不需要令牌，避免 Cloudflare 100 秒限制），并发 4、总时限 1800 秒；
`python3 scripts/set-mineru-profile.py lab` 删除这些覆盖，恢复实验室默认地址与并发 1。
切换后手动重启 Compiler 生效；脚本不管理 SSH、启动服务或改变登录自启。

## 删除文献（仅限本机维护）

公网站点不能删除文献：没有公开的删除接口，界面也不再提供删除按钮。确需彻底删除某篇文献
（全部版本、处理结果、检索数据与人工复核记录）时，在这台 Mac 上执行：

```bash
curl -X DELETE http://127.0.0.1:8100/api/internal/artifacts \
  -H 'content-type: application/json' \
  -d '{"artifactId":"artifact:<uuid>","confirmArtifactId":"artifact:<uuid>"}'
```

Tunnel 对所有 `/api/internal` 路径返回 404；该接口还会拒绝任何不是直接来自本机回环地址的请求。
