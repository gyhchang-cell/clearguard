"""Regression tests for rejection-only sampling and measured phase progress."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from clearguard import core

WORK = Path(__file__).resolve().parents[1] / 'work' / 'tests'
WORK.mkdir(parents=True, exist_ok=True)

class ScanProgressTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=WORK)
        self.root = Path(self.temp.name)
        self.markers = patch.object(core, 'PROJECT_MARKERS', {'PROTECTED_PROJECT'})
        self.markers.start()
        workspaces = patch.object(core, 'MANAGED_WORKSPACES', ())
        workspaces.start()
        self.addCleanup(workspaces.stop)
    def tearDown(self):
        self.markers.stop()
        self.temp.cleanup()
    def file(self, name, data):
        path = self.root / name
        path.write_bytes(data)
        return path
    def test_sample_match_is_not_duplicate_proof(self):
        data = b'a' * (1024 * 1024)
        a = self.file('a', data)
        changed = bytearray(data); changed[100000] = 98
        b = self.file('b', changed)
        self.assertEqual(core.sample(core.Snapshot.read(a)), core.sample(core.Snapshot.read(b)))
        self.assertFalse(core.scan(self.root).groups)
    def test_sample_and_hash_collision_still_require_bytes(self):
        self.file('a', b'a' * 200000); self.file('b', b'b' * 200000)
        with patch.object(core, 'sample', return_value=b'collision'), patch.object(core, 'digest', return_value='collision'):
            self.assertFalse(core.scan(self.root).groups)
    def test_different_samples_avoid_full_reads_but_duplicates_keep_full_hash(self):
        data = b'a' * 200000
        self.file('a', data); self.file('copy', data)
        self.file('b', b'b' * 200000); self.file('c', b'c' * 200000)
        with patch.object(core, 'digest', wraps=core.digest) as hashed:
            result = core.scan(self.root)
        self.assertEqual({call.args[0].path.name for call in hashed.call_args_list}, {'a','copy'})
        self.assertEqual(len(result.groups), 1)
        self.assertEqual(result.potential, len(data))
    def test_hash_progress_counts_actual_bytes_and_phase_totals(self):
        data = b'x' * (core.CHUNK * 2 + 17)
        self.file('a',data); self.file('b',data)
        events = []
        result = core.scan(self.root, progress=events.append)
        self.assertFalse(result.errors)
        self.assertTrue(all(isinstance(e,core.Progress) for e in events))
        discovery = [e for e in events if e.phase == 'discover']
        self.assertTrue(discovery and all(e.total is None for e in discovery))
        for phase in ('sample','hash','compare'):
            phase_events = [e for e in events if e.phase == phase]
            values = [e.completed for e in phase_events]
            self.assertEqual(values, sorted(values))
            self.assertEqual(values[-1], phase_events[-1].total)
        hashes = [e for e in events if e.phase == 'hash']
        self.assertEqual(hashes[-1].total, 2 * len(data))
        self.assertEqual([e.completed for e in hashes], [0, core.CHUNK, core.CHUNK*2, len(data), len(data)+core.CHUNK, len(data)+core.CHUNK*2, len(data)*2])
        self.assertEqual(events[-1].phase, 'done')
    def test_cancel_during_hash_never_reports_done_or_authorizes_cleanup(self):
        self.file('a',b'a'*(core.CHUNK*3)); self.file('b',b'a'*(core.CHUNK*3))
        event = threading.Event(); events=[]
        def progress(p):
            events.append(p)
            if p.phase == 'hash' and p.completed: event.set()
        result = core.scan(self.root, event, progress)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.groups)
        self.assertFalse(any(p.phase == 'done' for p in events))
    def test_changed_sample_rejected(self):
        a = self.file('a',b'a'*200000)
        snap = core.Snapshot.read(a)
        a.write_bytes(b'b'*200000)
        with self.assertRaises(core.UnsafeFile): core.sample(snap)
    def test_new_project_marker_before_content_read_blocks_duplicate(self):
        self.file('a',b'a'*200000); self.file('b',b'a'*200000)
        def progress(p):
            if p.phase == 'sample': (self.root/'PROTECTED_PROJECT').touch()
        result = core.scan(self.root, progress=progress)
        self.assertFalse(result.groups)
        self.assertTrue(result.errors)
    def test_compare_checks_open_handle_identity(self):
        a=self.file('a',b'abc'); b=self.file('b',b'abc')
        left,right=core.Snapshot.read(a),core.Snapshot.read(b)
        with patch.object(core.Snapshot,'verify_handle',side_effect=core.UnsafeFile('changed handle')):
            with self.assertRaises(core.UnsafeFile): core.equal(left,right)

    def test_later_batch_recovers_if_first_keeper_disappears(self):
        data = b'a' * 200000
        keeper = self.file('a', data)
        copies = {self.file(f'copy-{i:02}', data)
                  for i in range(core.COMPARE_BATCH + 2)}
        events = []

        def progress(event):
            events.append(event)
            if event.phase == 'compare' and event.completed == core.COMPARE_BATCH:
                keeper.unlink(missing_ok=True)

        result = core.scan(self.root, progress=progress)
        self.assertEqual(len(result.groups), 1)
        self.assertEqual({item.path for item in result.groups[0].files}, copies)
        self.assertTrue(result.errors)
        self.assertEqual([p.completed for p in events if p.phase == 'compare'],
                         [0, core.COMPARE_BATCH, len(copies)])

    def test_directory_marker_added_mid_hash_invalidates_cached_check(self):
        first = self.file('a', b'a' * 8192)
        self.file('b', b'a' * 8192)

        def progress(event):
            if event.phase == 'hash' and event.completed == first.stat().st_size:
                (self.root / 'PROTECTED_PROJECT').touch()

        result = core.scan(self.root, progress=progress)
        self.assertFalse(result.groups)
        self.assertTrue(result.errors)

    def test_large_group_reads_keeper_once_per_batch(self):
        from contextlib import contextmanager
        data = b'd' * 200000
        keeper = self.file('a', data)
        items = [core.Snapshot.read(self.file(f'copy-{i}', data))
                 for i in range(core.COMPARE_BATCH)]
        opened = []
        original = core._open_checked

        @contextmanager
        def record_open(item, checked=None):
            opened.append(item.path)
            with original(item, checked) as stream:
                yield stream

        with patch.object(core, '_open_checked', record_open):
            self.assertTrue(core._equal_batch(core.Snapshot.read(keeper), items, None, {}))
        self.assertEqual(opened, [keeper, *(item.path for item in items)])

    def test_large_files_are_grouped_in_one_pass_with_early_rejection(self):
        import hashlib
        size = core.STREAM_THRESHOLD + 17
        content = b'a' * size
        self.file('first.mkv', content)
        self.file('other-name.data', content)
        noisy = bytearray(content)
        noisy[250000] = 98
        self.file('near-match.mkv', noisy)
        events = []
        result = core.scan(self.root, progress=events.append)
        self.assertEqual(len(result.groups), 1)
        group = result.groups[0]
        self.assertEqual([f.path.name for f in group.files], ['first.mkv', 'other-name.data'])
        self.assertEqual(group.sha256, hashlib.sha256(content).hexdigest())
        ticks = [p for p in events if p.phase == 'stream']
        self.assertEqual(ticks[-1].completed, 3 * size)
        self.assertEqual(ticks[-1].read_bytes, 2 * size + core.CHUNK)
        self.assertEqual([p.completed for p in ticks], sorted(p.completed for p in ticks))

    def test_large_bucket_merges_exact_groups_across_batches(self):
        # Tighten threshold and batch size to force separate cohorts cheaply.
        data = b'a' * 200000
        different = bytearray(data)
        different[40000] = 98
        expected = {}
        for i in range(6):
            path = self.file(f'item-{i}', data if i % 2 else different)
            expected.setdefault(i % 2, set()).add(path)
        with patch.object(core, 'STREAM_THRESHOLD', 100000), patch.object(core, 'COMPARE_BATCH', 2):
            result = core.scan(self.root)
        self.assertFalse(result.errors)
        self.assertEqual({frozenset(f.path for f in g.files) for g in result.groups},
                         {frozenset(group) for group in expected.values()})

    def test_cancel_during_large_stream_never_authorizes_cleanup(self):
        self.file('first', b'a' * core.STREAM_THRESHOLD)
        self.file('copy', b'a' * core.STREAM_THRESHOLD)
        cancelled = threading.Event()

        def progress(event):
            if event.phase == 'stream' and event.read_bytes:
                cancelled.set()

        result = core.scan(self.root, cancelled, progress)
        self.assertTrue(result.cancelled)
        self.assertFalse(result.groups)
