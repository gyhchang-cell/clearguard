"""Developer QA of the actual frozen program. Never cleans personal files.

Only disk-discovery metadata uses real system APIs. All scanning and cleanup
fixtures live in a new, private directory inside the specified project QA root.
The native Trash function is NOT called by this test.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
from unittest.mock import patch

from . import core
from .app import App


def run(qa_root: Path, report_path: Path, ready: Path | None = None, proceed: Path | None = None):
    qa_root = qa_root.absolute()
    report_path = report_path.absolute()
    qa_root.mkdir(parents=True, exist_ok=True)
    # The caller explicitly supplies an existing, project-contained QA location.
    # Avoid accidental filesystem-root fixture creation.
    if qa_root == Path(qa_root.anchor):
        raise ValueError('QA directory cannot be a system root')
    if ready is not None and proceed is not None:
        ready.write_text('Ready; waiting for another isolated build', encoding='utf-8')
        deadline = time.monotonic() + 120
        while not proceed.exists():
            if time.monotonic() > deadline:
                raise TimeoutError('QA build-isolation signal did not arrive')
            time.sleep(0.05)
    checks = []
    scope = core.discover_computer_scope()  # also loads the real frozen psutil C extension
    assert scope.roots
    checks.append('native_disk_discovery')
    import send2trash
    assert callable(send2trash.send2trash)
    checks.append('native_trash_dependency_import_only')
    window = None
    with tempfile.TemporaryDirectory(dir=qa_root, prefix='frozen-flow-') as tmp:
        fixture = Path(tmp)
        disk_a, disk_b = fixture / 'disk-a', fixture / 'disk-b'
        disk_a.mkdir(); disk_b.mkdir()
        original, copy = disk_a / 'original.txt', disk_b / 'copy.txt'
        data = b'ClearGuard disposable QA fixture\n' * 100
        original.write_bytes(data); copy.write_bytes(data)
        (disk_b / 'unique.txt').write_bytes(b'Unique file, never a cleanup candidate')
        excluded = disk_a / 'protected-project'
        excluded.mkdir(); (excluded / 'PROTECTED_QA_PROJECT').write_bytes(b'')
        (excluded / 'keep.txt').write_bytes(data)
        # Fixtures are inside our own protected source project. Only substitute
        # project markers while scanning THESE generated fixture roots.
        ui_errors = []
        with (patch.object(core, 'MANAGED_WORKSPACES', ()),
              patch.object(core, 'PROJECT_MARKERS', {'PROTECTED_QA_PROJECT'}),
              patch('clearguard.app.messagebox.showerror', side_effect=lambda *args, **kwargs: ui_errors.append(args))):
            try:
                window = tk.Tk()
                app = App(window)
                window.update()
                assert not app.chosen and str(app.clear['state']) == 'disabled'
                checks.append('default_empty_selection')

                def settle():
                    deadline = time.monotonic() + 20
                    while app.busy and time.monotonic() < deadline:
                        window.update()
                        time.sleep(0.01)
                    assert not app.busy, 'UI worker did not finish'
                    assert app.result is not None, (app.status.get(), ui_errors)

                # Folder button uses the real worker/scanner/hash code.
                app.folder.set(str(disk_a))
                with patch('clearguard.app.filedialog.askdirectory', return_value=str(disk_a)):
                    app.start.invoke(); settle()
                assert app.result.scanned == 1 and not app.result.groups
                assert app.result.skipped >= 1
                checks.append('folder_scan_and_project_protection')

                # Only discovery is redirected to our disposable roots. The
                # whole-computer button, worker, indexing and comparisons are real.
                safe_scope = core.ComputerScope((disk_a, disk_b), (), ())
                with patch.object(core, 'discover_computer_scope', return_value=safe_scope):
                    app.folder.set('')
                    app.computer.invoke(); settle()
                result = app.result
                assert result.mode == 'computer' and result.scanned == 3
                assert len(result.groups) == 1 and len(result.groups[0].files) == 2
                assert result.groups[0].sha256 == hashlib.sha256(data).hexdigest()
                assert result.potential == len(data)
                assert not app.chosen and str(app.clear['state']) == 'disabled'
                checks.append('computer_button_real_cross_disk_scan_and_hash')

                assert len(app.rows) == 2
                app.select_all.invoke()
                assert len(app.chosen) == 1
                app.select_all.invoke()
                assert not app.chosen
                row = next(key for key, item in app.rows.items() if item.path == copy)
                app.tree.selection_set(row)
                app.toggle()
                app.clear.invoke()
                settle()
                dialogs = [w for w in window.winfo_children() if isinstance(w, tk.Toplevel)]
                assert len(dialogs) == 1
                from tkinter import ttk
                buttons = [w for w in dialogs[0].winfo_children() if isinstance(w, ttk.Button)]
                execute_button = next(w for w in buttons if '确认移入回收站' == str(w['text']))
                assert str(execute_button['state']) == 'disabled'
                assert original.exists() and copy.exists()
                checks.append('direct_and_bulk_selection_with_unchecked_consent')

                # Exercise the actual confirmation button and automatic audit
                # under the QA directory, with a fixture-only Trash adapter.
                restored = fixture / 'fixture-trash.txt'
                def fixture_execute(plan, consent, audit):
                    return core.execute(plan, consent, audit, trash=lambda p: Path(p).rename(restored))

                check = next(w for w in dialogs[0].winfo_children() if isinstance(w, ttk.Checkbutton))
                check.invoke()
                assert str(execute_button['state']) == 'normal'
                with (patch('clearguard.app.audit_directory', return_value=fixture / 'audit'),
                      patch('clearguard.app.execute', side_effect=fixture_execute),
                      patch('clearguard.app.messagebox.showinfo')):
                    execute_button.invoke()
                    deadline = time.monotonic() + 20
                    while app.busy and time.monotonic() < deadline:
                        window.update()
                        time.sleep(0.01)
                    assert not app.busy and not ui_errors, ui_errors
                audit = next((fixture / 'audit').glob('clearguard-*.jsonl'))
                assert 'trashed' in audit.read_text(encoding='utf-8')
                assert original.exists() and restored.read_bytes() == data
                restored.rename(copy)
                assert original.read_bytes() == copy.read_bytes()
                checks.append('fixture_only_confirmation_auto_audit_and_restore')

                previous_progress = app.report_progress
                def cancel_on_directory(message):
                    previous_progress(message)
                    if message.startswith('正在遍历'):
                        app.cancel.set()
                app.report_progress = cancel_on_directory
                with patch.object(core, 'discover_computer_scope', return_value=safe_scope):
                    app.computer.invoke(); settle()
                assert app.result.cancelled and not app.result.groups
                assert not app.chosen and str(app.clear['state']) == 'disabled'
                assert original.exists() and copy.exists()
                checks.append('real_worker_cancel_discards_cleanup_candidates')
            finally:
                if window is not None:
                    window.destroy()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({'status': 'passed', 'checks': checks,
                                      'real_disk_file_scan': False,
                                      'native_trash_called': False}, indent=2), encoding='utf-8')
