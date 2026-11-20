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
root.destroy()
print('GUI smoke passed: default empty selection, review dialog; no cleanup.')
