"""Exercise actual Tk UI; never scans or cleans personal files."""
import sys
from pathlib import Path
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
    p = folder / name; p.write_text('Disposable ClearGuard GUI fixture', encoding='utf-8')
    s = p.stat()
    files.append(Snapshot(p, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_dev, s.st_ino, s.st_nlink))
app.result = ScanResult(folder, groups=[DuplicateGroup('demo', 'demo-not-real-hash', tuple(files))], scanned=2)
app.populate(); root.update()
assert str(app.clear['state']) == 'disabled'
row = next(iter(app.rows)); app.tree.selection_set(row); app.toggle()
assert len(app.chosen) == 1
app.confirm(); root.update()
assert any(isinstance(w, tk.Toplevel) for w in root.winfo_children())
# Exercise the new button without scanning any real disk or user folder.
for child in root.winfo_children():
    if isinstance(child, tk.Toplevel): child.destroy()
from unittest.mock import patch
import time
from clearguard.core import ScanResult
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
    assert '全机扫描' in app.stats.get()
root.destroy()
print('GUI smoke passed: empty selection, review dialog, computer-scan button and worker result; no real disk scan or cleanup.')
