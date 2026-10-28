"""Offline cleanup safety checks; never use the real runtime."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cleanup = load('cleanup', 'scripts/clean_runtime.py')


class CleanupTests(unittest.TestCase):
    def test_inventory_keeps_dependencies_and_secrets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, installed = root / 'repo', root / 'installed'
            for path in ('data/compiler', 'data/wrangler', 'secrets', 'venv', 'app/dist', 'app/dist.old', 'runtime-home/.cache/huggingface', 'backups'):
                (installed / path).mkdir(parents=True)
            repo.mkdir()
            paths = cleanup.inventory(repo, installed)
            self.assertIn(installed / 'data/compiler', paths)
            self.assertIn(installed / 'app/dist.old', paths)
            for path in ('secrets', 'venv', 'app/dist', 'runtime-home/.cache/huggingface'):
                self.assertNotIn(installed / path, paths)

    def test_rejects_intermediate_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'outside').mkdir()
            (root / 'data').symlink_to(root / 'outside')
            with self.assertRaises(ValueError):
                cleanup.safe_path(root, 'data/compiler')

    def test_wipe_requires_exact_project_id(self):
        manifest = {'project_id': 'deepshovel-evidence'}
        for confirmation in ('', 'yes', 'DEEPSHOVEL-EVIDENCE', 'deepshovel-evidence '):
            with self.assertRaises(ValueError):
                cleanup.confirm_wipe(manifest, confirmation)
        cleanup.confirm_wipe(manifest, 'deepshovel-evidence')

    def test_target_symlink_does_not_expand(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo, installed = root / 'repo', root / 'installed'
            repo.mkdir()
            installed.mkdir()
            outside = root / 'outside'
            outside.mkdir()
            (outside / 'keep').write_text('keep')
            (repo / 'outputs').symlink_to(outside)
            paths = cleanup.inventory(repo, installed)
            self.assertIn(repo / 'outputs', paths)
            (repo / 'outputs').unlink()
            self.assertEqual((outside / 'keep').read_text(), 'keep')



if __name__ == '__main__':
    unittest.main()
