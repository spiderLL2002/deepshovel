#!/usr/bin/env python3
"""Install only Compile result synchronization without restarting active services."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import public_manifest as deployment


def main() -> None:
    manifest = deployment.load_manifest(require_public=True)
    root = Path.home() / 'Library/Application Support' / manifest['install_directory']
    deployment.registry_check(manifest, root)
    entry = deployment.load_registry()['projects'].get(manifest['project_id'])
    if not entry or not entry.get('tunnel_id'):
        raise SystemExit('An owned, installed public runtime is required')
    label = manifest['service_namespace'] + '.compile-sync'
    domain = f'gui/{os.getuid()}'
    with tempfile.TemporaryDirectory(prefix='deepshovel-sync-install-') as tmp:
        stage = Path(tmp)
        deployment.generate_runtime(manifest, stage, root)
        deployment.generate_plists(manifest, stage, root,
            Path(shutil.which('cloudflared') or '/opt/homebrew/bin/cloudflared'),
            Path.home() / f"Applications/{manifest['display_name']} Control.app/Contents/MacOS/DeepShovelControl")
        plist = stage / f'{label}.plist'
        subprocess.run(['plutil', '-lint', str(plist)], check=True)
        # Only the new synchronizer may be unloaded. Never restart its dependencies.
        if subprocess.run(['launchctl', 'print', f'{domain}/{label}'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            subprocess.run(['launchctl', 'bootout', f'{domain}/{label}'], check=True)
        for source, destination in [
            (deployment.ROOT / 'deploy/runtime/compile-sync.py', root / 'app/deploy/runtime/compile-sync.py'),
            (deployment.ROOT / 'deploy/runtime/service-control.sh', root / 'app/deploy/runtime/service-control.sh'),
            (stage / 'compile-sync.json', root / 'app/deploy/generated/compile-sync.json'),
            (plist, Path.home() / 'Library/LaunchAgents' / plist.name),
        ]:
            temporary = destination.with_suffix(destination.suffix + '.next')
            shutil.copy2(source, temporary)
            temporary.replace(destination)
        deployment.registry_write(manifest, root, entry['tunnel_id'])
        subprocess.run(['launchctl', 'bootstrap', domain,
                        str(Path.home() / 'Library/LaunchAgents' / plist.name)], check=True)
        subprocess.run(['launchctl', 'print', f'{domain}/{label}'],
                       check=True, stdout=subprocess.DEVNULL)
    print('Compile result synchronization installed; existing services were not restarted.')


if __name__ == '__main__':
    main()
