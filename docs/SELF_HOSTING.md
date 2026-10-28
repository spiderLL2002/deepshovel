# Full application setup

The complete source tree can build without access to the maintainer's production database. Use Node 22.13 or newer and Python 3.11–3.13; the paper artifact reference environment is Python 3.12.13. The Web build uses the checked-in npm lockfile.

1. Run `npm run install:ci`.
2. Run `python3 scripts/setup-local-config.py`. This creates a new random local shared secret and a Compiler template only if each file is absent. It never prints the secret or copies maintainer credentials.
3. Create `compiler/.venv` with Python 3.12 and install `-e './compiler[test]'` from the repository root.
4. Edit `compiler/.env.local` for your own service access. The published source uses the model IDs, Responses protocol, endpoint and parser allowlist recorded in `compiler/compiler_service/config.py`; an arbitrary OpenAI-compatible endpoint is not asserted to be interchangeable. Use a compatible service or adapt that integration explicitly. Nothing grants access to the maintainer's relay keys.
5. Start `npm run dev:compiler` and `npm run dev` in separate terminals. The latter migrates a new local D1 database and prints the browser URL. Local D1/R2 emulation does not require a Cloudflare login.

The full Compiler downloads retrieval/model dependencies when configured. An online run can consume paid API quota. Full Python dependencies are specified by `compiler/pyproject.toml`; unlike the paper's small replay environment, this GPU/model-capable application environment is not claimed to have a single cross-platform fully locked install.

Use `npm run build` to build the Web artifact, `npm run contract:check` for shared contracts, and `npm run test:node` for existing Web unit tests. The build checks the Worker entry, migrations and PDF.js assets. These checks do not reproduce the live 9,000-paper corpus or the stochastic behavior of external models.

The macOS `make public-*` targets describe the maintainer's host configuration. They are not required to run the application or the paper artifact. Do not run them against the maintainer's domain from a new clone. A new host needs its own deployment configuration, storage, services and credentials.
