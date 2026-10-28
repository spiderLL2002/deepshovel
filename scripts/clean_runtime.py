#!/usr/bin/env python3
"""List project data by default; --apply clears it after services are stopped.

--apply irreversibly deletes the whole paper library (D1, R2 originals, Compiler
results and indexes). It therefore also requires --confirm-project-id with the
exact project_id from deploy/local-public.json.
Does not delete current source, dependencies, secrets or Tunnel credentials.
Run public-update afterward to initialize D1 and required model resources.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

REPO_DATA = ('.compiler-data', 'compiler/.compiler-data', 'compiler/benchmarks',
             '.wrangler/state', '.wrangler/tmp', '.wrangler/registry',
             '.wrangler/wrangler.log', 'compiler/.sites-runtime',
             'node_modules/.vite', 'node_modules/.vite-temp', 'tsconfig.tsbuildinfo',
             'outputs', 'data/originals', 'data/acceptance', '.pytest_cache',
             '.ruff_cache', 'compiler/.pytest_cache', 'compiler/.ruff_cache',
             '.project-runtime/tmp', '.project-runtime/npm-cache',
             '.project-runtime/xdg-config/.wrangler/logs',
             '.project-runtime/xdg-config/.wrangler/registry',
             '.project-runtime/wrangler/logs', '.project-runtime/wrangler/registry')
INSTALLED_DATA = ('data/compiler', 'data/wrangler', 'data/compile-sync', 'compiler-data', 'state',
                  'logs', 'backups', 'review-custody', 'runtime-home/tmp',
                  'runtime-home/config/.wrangler/logs',
                  'runtime-home/config/.wrangler/registry')
BACKUPS = ('app.previous*', 'app.rollback*', 'app.next*', 'venv.previous*',
           'venv.rollback*', 'venv.next*', 'gpt-fallback-*', 'luna-migration-*',
           'luna-perf-*', 'no-llm-timeout-*', 'top30-backup-*')


def safe_path(root, relative):
    path = root / relative
    # A link at the deletion target is unlinked, never traversed. Intermediate
    # links are rejected so a parent link cannot redirect deletion elsewhere.
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ValueError(f'Refusing symlink parent: {parent}')
    return path


def inventory(repo, installed):
    result = []
    for root, names in ((repo, REPO_DATA), (installed, INSTALLED_DATA)):
        result.extend(safe_path(root, name) for name in names)
    if installed.exists():
        for pattern in BACKUPS:
            result.extend(installed.glob(pattern))
        app = safe_path(installed, 'app')
        if not app.is_symlink():
            for pattern in ('dist.*', 'dist-*'):
                result.extend(app.glob(pattern))
    # Walk only current source and tests, never dependency environments.
    for relative in ('compiler/compiler_service', 'compiler/tests', 'scripts', 'tests', 'deploy'):
        source = safe_path(repo, relative)
        if source.exists() and not source.is_symlink():
            for base, dirs, _ in os.walk(source, followlinks=False):
                for name in list(dirs):
                    if name == '__pycache__':
                        result.append(Path(base) / name)
                        dirs.remove(name)
    return sorted(set(p for p in result if p.exists() or p.is_symlink()))


def ensure_stopped(manifest):
    if sys.platform != 'darwin':
        raise ValueError('--apply is supported only on the deployment Mac')
    namespace = manifest['service_namespace']
    for component in ('watchdog', 'tunnel', 'service', 'control', 'compile-sync'):
        label = f'gui/{os.getuid()}/{namespace}.{component}'
        status = subprocess.run(['launchctl', 'print', label], capture_output=True)
        if status.returncode == 0:
            raise ValueError(f'Stop this project LaunchAgent before cleanup: {label}')
    for port in (manifest['local_port'], manifest['compiler_port']):
        status = subprocess.run(['lsof', '-nP', f'-iTCP:{port}', '-sTCP:LISTEN'], capture_output=True)
        if status.returncode == 0:
            raise ValueError(f'Port {port} is still listening; stop the project before cleanup')


def confirm_wipe(manifest, confirmation):
    """Deleting the library must be a deliberate, typed decision."""
    if confirmation != manifest['project_id']:
        raise ValueError(
            'Wiping deletes every paper and result. Re-run with '
            f'--confirm-project-id {manifest["project_id"]} to confirm.'
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='irreversibly clear listed project data')
    parser.add_argument('--confirm-project-id', default='',
                        help='required with --apply: the exact project_id being wiped')
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    manifest_path = repo / 'deploy/local-public.json'
    manifest = json.loads(manifest_path.read_text())
    installed = Path.home() / 'Library/Application Support' / manifest['install_directory']
    if installed.is_symlink():
        raise ValueError('Refusing symlink installed root')
    if installed.exists():
        owner_path = safe_path(installed, 'app/deploy/generated/deployment-owner.json')
        owner = json.loads(owner_path.read_text())
        if owner.get('project_id') != manifest['project_id'] or owner.get('source_manifest') != str(manifest_path):
            raise ValueError('Installed runtime ownership does not match this repository')
    paths = inventory(repo, installed)
    report = {'mode': 'apply' if args.apply else 'dry-run', 'delete': [str(p) for p in paths],
              'preserve': [str(installed / p) for p in ('app', 'venv', 'secrets', 'cloudflared', 'runtime-home/.cache', 'runtime-home/config')],
              'review_separately': ['.project-runtime/xdg-config and runtime-home/config (Wrangler configuration may contain credentials)',
                                    'runtime-home/.cache/huggingface (model weights)',
                                    'data outside the named project paths']}
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if not args.apply:
        return
    confirm_wipe(manifest, args.confirm_project_id)
    ensure_stopped(manifest)
    if not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError('This Python lacks symlink-safe directory deletion')
    for path in paths:
        root = installed if path.is_relative_to(installed) else repo
        safe_path(root, path.relative_to(root))
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    if installed.exists():
        for name in ('data/compiler', 'data/wrangler', 'logs', 'runtime-home/tmp'):
            safe_path(installed, name).mkdir(parents=True, exist_ok=True)
        # Do not reimport development data during the next deployment.
        safe_path(installed, 'data/.initial-import-complete').touch()
    print('Cleared listed data. Run make public-update to initialize empty database schemas and restart.')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError) as error:
        print(f'Cleanup refused: {error}', file=sys.stderr)
        sys.exit(1)
