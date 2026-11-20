"""Exercise actual Tk UI; never scans or cleans personal files."""
import sys
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import tkinter as tk
from clearguard.app import App
from clearguard.core import ScanResult, Snapshot, DuplicateGroup
root = tk.Tk()
app = App(root)
folder = Path(__file__).resolve().parents[1] / 'work' / 'gui-fixture'
folder.mkdir(parents=True, exist_ok=True)
files = []
for name in ('demo-original.txt', 'demo-copy.txt'):
    p = (folder / 'disposable' if 'copy' in name else folder) / name
    p.parent.mkdir(exist_ok=True)
    p.write_text('Disposable ClearGuard GUI fixture', encoding='utf-8')
    with patch('clearguard.core.PROJECT_MARKERS', {'PROTECTED_PROJECT'}), patch('clearguard.core.MANAGED_WORKSPACES', ()):
        files.append(Snapshot.read(p))
app.result = ScanResult(folder, groups=[DuplicateGroup('demo', 'demo-not-real-hash', tuple(files))], scanned=2)
app.populate(); root.update()
assert str(app.clear['state']) == 'disabled'
assert '每组留一份' in app.size_summary.get()
parent = app.tree.get_children()[0]
assert app.tree.set(parent, 'choice') == '2 份内容相同'
assert all(app.tree.set(row, 'choice') == '□ 可勾选'
           for row in app.tree.get_children(parent))
first, second = app.tree.get_children(parent)
app.tree.selection_set(second)
app.toggle()
assert app.chosen == {files[1].path}
app.tree.selection_set(first)
app.toggle()
assert app.chosen == {files[1].path}
assert app.tree.set(first, 'choice') == '保留这份'
assert app.all_checked.get()
app.select_all.invoke()
assert not app.chosen
app.select_all.invoke()
assert app.chosen == {files[1].path}
app.query.set('absent path')
app.apply_filter()
assert not app.tree.get_children()
assert len(app.chosen) == 1
app.query.set('demo-copy')
app.apply_filter()
assert len(app.tree.get_children()) == 1
assert sum(app.tree.set(row, 'choice') == '☑ 已勾选' for row in app.rows) == 1
app.query.set('')
app.apply_filter()
with (patch('clearguard.core.PROJECT_MARKERS', {'PROTECTED_PROJECT'}),
      patch('clearguard.core.MANAGED_WORKSPACES', ()),
      patch('clearguard.app.messagebox.showerror', side_effect=AssertionError('Confirmation dialog failed'))):
    app.confirm()
    deadline = time.monotonic() + 10
    while app.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    assert not app.busy
assert any(isinstance(w, tk.Toplevel) for w in root.winfo_children())
# Exercise the new button without scanning any real disk or user folder.
for child in root.winfo_children():
    if isinstance(child, tk.Toplevel): child.destroy()
machine_result = ScanResult(folder, roots=(folder,), mode='computer')
app.folder.set('')
with patch('clearguard.app.scan_computer', return_value=machine_result) as machine_scan:
    app.computer.invoke()
    assert app.busy
    assert str(app.computer['state']) == 'disabled'
    assert str(app.stop['state']) == 'normal'
    deadline = time.monotonic() + 5
    while app.busy and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    machine_scan.assert_called_once()
    assert not app.busy and app.result.mode == 'computer'
    assert not app.chosen and str(app.clear['state']) == 'disabled'
    assert app.scope.get() == '全机扫描'
    assert app.stats.get() == ''
    assert float(app.progress['value']) == 100
# Numeric bar values must come only from measured events, without a timer.
from clearguard.core import Progress
app.lock(True)
app.report_progress(Progress('unknown total', 'discover', 123))
app.poll()
assert float(app.progress['value']) == 0
app.report_progress(Progress('actual bytes', 'hash', 25, 100, '字节'))
app.poll()
assert float(app.progress['value']) == 25
app.report_progress(Progress('new phase', 'compare', 0, 5))
app.poll()
assert float(app.progress['value']) == 0
app.report_progress(Progress('actual files', 'compare', 3, 5))
app.poll()
assert float(app.progress['value']) == 60
app.lock(False)
# The review actions and pagination must survive the minimum window height.
root.geometry('830x500')
root.update()
assert app.clear.winfo_ismapped() and app.select_all.winfo_ismapped()
assert app.next_page.winfo_ismapped() and app.tree.winfo_height() > 100
root.destroy()
print('GUI smoke passed: empty selection, review dialog, computer-scan button and worker result; no real disk scan or cleanup.')
