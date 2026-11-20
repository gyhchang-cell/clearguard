import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from clearguard import core

WORK = Path(__file__).resolve().parents[1] / 'work' / 'tests'
WORK.mkdir(parents=True, exist_ok=True)

class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=WORK)
        self.root = Path(self.temp.name)
        # Test fixtures live inside this source project. Substitute ancestor
        # markers only in tests, never in production. Test real policy below.
        self.markers = patch.object(core, 'PROJECT_MARKERS', {'PROTECTED_PROJECT'})
        self.markers.start()
    def tearDown(self):
        self.markers.stop()
        self.temp.cleanup()
    def file(self, name, data=b'content'):
        p = self.root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p
    def setup_plan(self, third=False):
        self.a, self.b = self.file('a'), self.file('b')
        if third:
            self.c = self.file('c')
        self.r = core.scan(self.root)
        self.plan = core.make_plan(self.r, [self.b, self.c] if third else [self.b])
        self.calls = []
    def execute(self, trash=None, audit=None, consent=None):
        return core.execute(self.plan, consent if consent is not None else f'TRASH {self.plan.id}',
                            audit or self.root / 'audit', trash or self.calls.append)
    def test_real_bytes_not_names_or_sizes(self):
        self.file('a', b'abc'); self.file('b', b'xyz'); self.file('c', b'abc')
        r = core.scan(self.root)
        self.assertEqual(len(r.groups), 1)
        self.assertEqual({f.path.name for f in r.groups[0].files}, {'a', 'c'})
        self.assertEqual(r.potential, 3)
    def test_hash_collision_still_requires_byte_equality(self):
        self.file('a', b'abc'); self.file('b', b'xyz')
        with patch.object(core, 'digest', return_value='collision'):
            self.assertFalse(core.scan(self.root).groups)
    def test_no_selection_and_keep_one(self):
        self.setup_plan()
        for chosen in ([], [self.a, self.b], [self.root / 'unknown']):
            with self.assertRaises(core.UnsafeFile): core.make_plan(self.r, chosen)
    def test_no_consent_no_mutation(self):
        self.setup_plan()
        with self.assertRaises(core.UnsafeFile): self.execute(consent='yes')
        self.assertFalse(self.calls)
        self.assertTrue(self.b.exists())
    def test_approved_plan_logs_and_keeps_original(self):
        self.setup_plan()
        trash = self.root / 'fixture-bin'; trash.mkdir()
        out = self.execute(lambda p: Path(p).rename(trash / Path(p).name))
        self.assertEqual(out[0]['status'], 'trashed')
        self.assertTrue(self.a.exists())
        self.assertFalse(self.b.exists())
        self.assertTrue((trash / self.b.name).exists())
        self.assertIn('intent', (self.root / 'audit').read_text())
    def test_modified_target_aborts(self):
        self.setup_plan(); self.b.write_bytes(b'changed')
        with self.assertRaises(core.UnsafeFile): self.execute()
        self.assertFalse(self.calls)
    def test_removed_keeper_aborts(self):
        self.setup_plan(); self.a.unlink()
        with self.assertRaises(OSError): self.execute()
        self.assertFalse(self.calls)
    def test_hardlinks_excluded(self):
        a = self.file('a'); self.file('regular'); os.link(a, self.root / 'hard')
        r = core.scan(self.root)
        self.assertFalse(r.groups)
        self.assertGreaterEqual(r.skipped, 2)
    def test_symlink_parent_replacement_rejected(self):
        folder = self.root / 'folder'; a = self.file('folder/a')
        item = core.Snapshot.read(a); folder.rename(self.root / 'old')
        try: folder.symlink_to(self.root / 'old', target_is_directory=True)
        except OSError: self.skipTest('Symlink privilege unavailable')
        with self.assertRaises(core.UnsafeFile): item.verify()
    def test_project_application_and_cloud_protected(self):
        self.file('dev/PROTECTED_PROJECT', b''); self.file('dev/a')
        self.file('Some.app/a'); self.file('OneDrive - Example/a'); self.file('regular')
        r = core.scan(self.root)
        self.assertEqual(r.scanned, 1)
        self.assertFalse(r.groups)
    def test_production_project_marker_protects_ancestor(self):
        with patch.object(core, 'PROJECT_MARKERS', {'pyproject.toml'}):
            with self.assertRaises(core.UnsafeFile): core.scan(self.root)
    def test_cancelled_scan_cannot_clean(self):
        self.file('a'); event = threading.Event(); event.set()
        r = core.scan(self.root, event)
        self.assertTrue(r.cancelled)
        with self.assertRaises(core.UnsafeFile): core.make_plan(r, [self.root / 'a'])
    def test_old_large_unique_is_review_only(self):
        a = self.file('unique'); r = core.scan(self.root, large_bytes=1)
        self.assertEqual(len(r.review), 1)
        with self.assertRaises(core.UnsafeFile): core.make_plan(r, [a])
    def test_trash_failure_never_falls_back(self):
        self.setup_plan()
        def fail(_): raise OSError('Trash unavailable')
        out = self.execute(fail)
        self.assertEqual(out[0]['status'], 'failed')
        self.assertTrue(self.a.exists() and self.b.exists())
    def test_expired_plan(self):
        self.setup_plan()
        with patch.object(core.time, 'monotonic', return_value=self.plan.created + 601):
            with self.assertRaises(core.UnsafeFile): self.execute()
    def test_audit_cannot_overwrite_existing_data(self):
        self.setup_plan()
        with self.assertRaises(FileExistsError): self.execute(audit=self.a)
        self.assertFalse(self.calls)
        self.assertEqual(self.a.read_bytes(), b'content')
    def test_all_targets_preflight_before_mutation(self):
        self.setup_plan(third=True); self.c.write_bytes(b'new data')
        with self.assertRaises(core.UnsafeFile): self.execute()
        self.assertFalse(self.calls)
    def test_keeper_disappears_mid_batch(self):
        self.setup_plan(third=True)
        def trash(p): self.calls.append(p); self.a.unlink()
        out = self.execute(trash)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(out[-1]['status'], 'failed')
        self.assertTrue(self.c.exists())
    def test_refuse_system_root(self):
        with self.assertRaises(core.UnsafeFile): core.scan(Path(self.root.anchor))

if __name__ == '__main__': unittest.main()
