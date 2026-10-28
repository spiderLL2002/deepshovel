#!/usr/bin/env python3
"""Create local-only configuration without copying any maintainer credentials."""
from pathlib import Path
import secrets
root = Path(__file__).resolve().parents[1]
shared = root / '.dev.vars'
if not shared.exists():
    shared.write_text('COMPILER_SHARED_SECRET=' + secrets.token_urlsafe(48) + '\n')
    shared.chmod(0o600)
    print('Created local shared configuration; value not printed.')
private = root / 'compiler/.env.local'
if not private.exists():
    private.write_text((root / 'compiler/.env.example').read_text())
    private.chmod(0o600)
    print('Created Compiler template; configure your own provider keys before online use.')
print('Existing files were preserved.')
