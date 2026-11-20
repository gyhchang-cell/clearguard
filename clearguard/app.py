"""Native desktop UI. All filesystem work runs outside the Tk main thread."""
from __future__ import annotations
import json
import queue
import shutil
import threading
import tkinter as tk
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .core import scan, scan_computer, make_plan, execute


def human(value):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if value < 1024:
            return f'{value:,.1f} {unit}'
        value /= 1024
    return f'{value:.1f} PiB'


class App:
    def __init__(self, root):
        self.root = root
        self.root.title('ClearGuard · 空间有数，清理有据')
        self.root.geometry('1120x760')
        self.root.minsize(1000, 580)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.result = None
        self.busy = False
        self.cleaning = False
        self.chosen = set()
        self.rows = {}
        self.folder = tk.StringVar()
        self.status = tk.StringVar(value='选择文件夹，或点击全机扫描。扫描只读，不会自动清理。')
        self.stats = tk.StringVar(value='所有文件保留原位 · 无上传 · 无后台自动清理')
        style = ttk.Style()
        style.configure('Title.TLabel', font=('Helvetica', 26, 'bold'))
        style.configure('Sub.TLabel', font=('Helvetica', 12))
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill='both', expand=True)
        ttk.Label(outer, text='ClearGuard', style='Title.TLabel').pack(anchor='w')
        ttk.Label(outer, text='先看证据，再决定清理。', style='Sub.TLabel').pack(anchor='w', pady=(4, 18))
        bar = ttk.Frame(outer)
        bar.pack(fill='x')
        self.entry = ttk.Entry(bar, textvariable=self.folder)
        self.entry.pack(side='left', fill='x', expand=True)
        self.pick = ttk.Button(bar, text='选择文件夹', command=self.choose)
        self.pick.pack(side='left', padx=8)
        self.start = ttk.Button(bar, text='开始扫描', command=self.begin)
        self.start.pack(side='left')
        self.computer = ttk.Button(bar, text='全机扫描', command=lambda: self.begin(whole_machine=True))
        self.computer.pack(side='left', padx=(8, 0))
        self.stop = ttk.Button(bar, text='取消扫描', command=self.cancel.set, state='disabled')
        self.stop.pack(side='left', padx=(8, 0))
        ttk.Label(outer, textvariable=self.stats, wraplength=1000).pack(anchor='w', pady=16)
        notebook = ttk.Notebook(outer)
        notebook.pack(fill='both', expand=True)
        dup = ttk.Frame(notebook, padding=8)
        review = ttk.Frame(notebook, padding=8)
        details = ttk.Frame(notebook, padding=8)
        notebook.add(dup, text='重复文件 · 可核验清理')
        notebook.add(review, text='大文件 / 较旧文件 · 仅供查看')
        notebook.add(details, text='规则与扫描问题')
        ttk.Label(dup, text='双击文件切换选择。每组第一份固定保留；其余文件均默认不选。').pack(anchor='w', pady=(0, 8))
        self.tree = self.table(dup, ('choice', 'size', 'path'), ('选择', '逻辑大小', '完整路径'))
        self.tree.column('#0', width=190)
        self.tree.column('choice', width=100, stretch=False)
        self.tree.column('size', width=110, stretch=False)
        self.tree.column('path', width=560)
        self.tree.bind('<Double-1>', self.toggle)
        self.tree.bind('<space>', self.toggle)
        ttk.Label(review, text='大于 100 MiB 或修改时间超过 180 天不代表无用；本页不提供删除。').pack(anchor='w', pady=(0, 8))
        self.review = self.table(review, ('size', 'path'), ('逻辑大小', '完整路径'))
        self.review.column('#0', width=160)
        self.review.column('size', width=110, stretch=False)
        self.review.column('path', width=650)
        self.notes = tk.Text(details, wrap='word', height=12)
        self.notes.pack(fill='both', expand=True)
        self.write_notes('')
        footer = ttk.Frame(outer)
        footer.pack(fill='x', pady=(14, 0))
        self.export = ttk.Button(footer, text='导出本地报告', command=self.save_report, state='disabled')
        self.export.pack(side='left')
        self.clear = ttk.Button(footer, text='检查所选并清理…', command=self.confirm, state='disabled')
        self.clear.pack(side='right')
        self.selection_label = ttk.Label(footer, text='已选 0 个文件')
        self.selection_label.pack(side='right', padx=16)
        self.progress = ttk.Progressbar(outer, mode='indeterminate')
        self.progress.pack(fill='x', pady=(14, 5))
        ttk.Label(outer, textvariable=self.status, wraplength=1000).pack(anchor='w')
        self.root.after(80, self.poll)

    def table(self, parent, columns, headings):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='tree headings', selectmode='browse')
        tree.heading('#0', text='分组 / 文件')
        for c, title in zip(columns, headings):
            tree.heading(c, text=title)
        scroll = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        return tree

    def choose(self):
        folder = filedialog.askdirectory(title='选择要扫描的个人文件夹')
        if folder:
            self.folder.set(folder)

    def lock(self, busy, cleaning=False):
        self.busy, self.cleaning = busy, cleaning
        for control in (self.start, self.computer, self.pick, self.entry):
            control.configure(state='disabled' if busy else 'normal')
        self.stop.configure(state='normal' if busy and not cleaning else 'disabled')
        self.export.configure(state='normal' if self.result and not busy else 'disabled')
        self.clear.configure(state='normal' if self.chosen and not busy else 'disabled')
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    def begin(self, whole_machine=False):
        if self.busy:
            return
        if not whole_machine and not self.folder.get().strip():
            self.choose()
            if not self.folder.get():
                return
        self.result = None
        self.chosen.clear()
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        self.review.delete(*self.review.get_children())
        self.update_selection()
        self.cancel.clear()
        self.latest_progress = None
        self.lock(True)
        self.status.set('正在发现本地磁盘…' if whole_machine else '正在扫描…')
        folder = Path(self.folder.get()).expanduser()
        def work():
            try:
                result = (scan_computer(self.cancel, self.report_progress) if whole_machine
                          else scan(folder, self.cancel, self.report_progress))
                self.events.put(('scan', result))
            except Exception as error:
                self.events.put(('error', str(error)))
        threading.Thread(target=work, daemon=True).start()

    def report_progress(self, text):
        # Coalesce progress so millions of files cannot fill the event queue.
        self.latest_progress = text

    def poll(self):
        if self.busy and not self.cleaning and getattr(self, 'latest_progress', None):
            self.status.set(self.latest_progress)
            self.latest_progress = None
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == 'scan':
                    self.result = payload
                    self.populate()
                    self.lock(False)
                elif kind == 'error':
                    self.lock(False)
                    self.status.set('操作未完成：' + payload)
                    messagebox.showerror('操作停止', payload)
                elif kind == 'clean':
                    success = sum(x['status'] == 'trashed' for x in payload)
                    self.status.set(f'已送入系统废纸篓/回收站：{success} 个。扫描结果已失效，请重新扫描。')
                    self.result = None
                    self.chosen.clear()
                    self.rows.clear()
                    self.tree.delete(*self.tree.get_children())
                    self.review.delete(*self.review.get_children())
                    self.update_selection()
                    self.lock(False)
                    failures = [x.get('error', '') for x in payload if x['status'] == 'failed']
                    messagebox.showinfo('清理结果', f'已送入回收站 {success} 个文件。\n可通过系统回收站恢复。\n移动到回收站不会立即释放磁盘空间。' + ('\n失败后已停止：' + '\n'.join(failures) if failures else ''))
        except queue.Empty:
            pass
        self.root.after(80, self.poll)

    def populate(self):
        r = self.result
        try:
            free = human(shutil.disk_usage(r.root).free)
        except OSError:
            free = '未知'
        scope_label = f'全机扫描 · {len(r.roots)} 个候选位置 · ' if r.mode == 'computer' else ''
        disk_label = '首个磁盘可用' if r.mode == 'computer' else '当前磁盘可用'
        self.stats.set(scope_label + f'读取 {r.scanned:,} 个文件 · 重复组 {len(r.groups):,} · 可审查重复数据 {human(r.potential)} · {disk_label} {free}')
        for i, group in enumerate(r.groups):
            parent = self.tree.insert('', 'end', text=f'重复组 {i+1} · {len(group.files)} 份', open=True)
            for j, item in enumerate(group.files):
                row = self.tree.insert(parent, 'end', text=item.path.name,
                                       values=('保留' if j == 0 else '□ 未选', human(item.size), str(item.path)))
                if j:
                    self.rows[row] = item
        for item in r.review:
            self.review.insert('', 'end', text=item.path.name, values=(human(item.size), str(item.path)))
        self.write_notes('扫描范围：\n' + '\n'.join(str(p) for p in (r.roots or (r.root,))) +
                         f'\n\n跳过受保护条目/目录：{r.skipped}\n未扫描位置：\n' + '\n'.join(r.excluded) +
                         f'\n\n扫描问题：{len(r.errors)}\n' + '\n'.join(r.errors))
        self.status.set('扫描已取消，结果不可用于清理。' if r.cancelled else '所列范围扫描结束，保护/无权限位置未扫描，详见规则与扫描问题。')

    def toggle(self, event=None):
        if self.busy:
            return
        selection = self.tree.selection()
        if not selection:
            return
        row = selection[0]
        item = self.rows.get(row)
        if item:
            if item.path in self.chosen:
                self.chosen.remove(item.path)
            else:
                self.chosen.add(item.path)
            self.tree.set(row, 'choice', '☑ 已选' if item.path in self.chosen else '□ 未选')
        self.update_selection()

    def update_selection(self):
        size = sum(f.size for f in self.rows.values() if f.path in self.chosen)
        self.selection_label.configure(text=f'已选 {len(self.chosen)} 个 · {human(size)}')
        self.clear.configure(state='normal' if self.chosen and not self.busy else 'disabled')

    def write_notes(self, extra):
        text = ('核验：文件大小 → 完整 SHA-256 → 逐字节比较 → 清理前再次验证。\n'
                '保护：系统目录、开发项目、应用包、已知云同步目录、占位文件、符号链接、硬链接。\n'
                '不根据名称、日期、扩展名或 AI 推断“无用”。不清空回收站，不永久删除。\n'
                '全机扫描：本机已挂载磁盘，含已挂载 USB；不进入网络盘、虚拟卷和系统内部卷。\n'
                '保护目录、拒绝访问、离线磁盘与未挂载位置不在实际覆盖范围内。\n'
                '数字为逻辑大小；APFS 克隆、压缩、稀疏文件和回收站使实际可释放空间不同。\n'
                '请在清理前停止同步/写入相关文件；程序无法原子锁住外部程序对路径的全部修改。\n'
                '运行内存由操作系统管理，本应用不终止进程，不提供“内存加速”。\n\n' + extra)
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', text)
        self.notes.configure(state='disabled')

    def confirm(self):
        try:
            plan = make_plan(self.result, list(self.chosen))
        except ValueError as error:
            messagebox.showerror('无法建立清理计划', str(error))
            return
        dialog = tk.Toplevel(self.root)
        dialog.title('确认本次清理')
        dialog.geometry('820x520')
        dialog.transient(self.root)
        dialog.grab_set()
        ttk.Label(dialog, text=f'将 {len(plan.targets)} 个文件（{human(plan.bytes)}）移入系统回收站', font=('Helvetica', 16, 'bold'), wraplength=770).pack(padx=20, pady=16)
        text = tk.Text(dialog, wrap='word')
        text.pack(fill='both', expand=True, padx=20)
        for g in plan.groups:
            text.insert('end', '\n保留：\n' + '\n'.join(str(f.path) for f in g.files if f.path not in self.chosen) + '\n')
            text.insert('end', '移入回收站：\n' + '\n'.join(str(f.path) for f in g.files if f.path in self.chosen) + '\nSHA-256: ' + g.sha256 + '\n')
        text.configure(state='disabled')
        consent = tk.BooleanVar(value=False)
        button = ttk.Button(dialog, text='确认移入回收站', state='disabled')
        ttk.Checkbutton(dialog, text='我已核对完整路径与保留副本，并同意本次清理。', variable=consent,
                        command=lambda: button.configure(state='normal' if consent.get() else 'disabled')).pack(pady=12)
        ttk.Label(dialog, text='可在系统回收站恢复；本操作不会立即释放磁盘空间。').pack()
        def approved():
            if not consent.get():
                return
            audit = filedialog.asksaveasfilename(parent=dialog, title='保存本次清理审计日志（仅保存在本机）',
                                               initialdir=str(Path.home() if self.result.mode == 'computer' else self.result.root), initialfile='clearguard-audit.jsonl', defaultextension='.jsonl')
            if not audit:
                return
            # Avoid writing an audit over any scanned file, including keepers.
            audit_path = Path(audit).absolute()
            if audit_path.exists() or any(audit_path == f.path for g in plan.groups for f in g.files):
                messagebox.showerror('请选择新的日志文件', '审计日志不能覆盖现有文件。', parent=dialog)
                return
            dialog.destroy()
            self.lock(True, cleaning=True)
            self.status.set('正在复核并移动所选文件，请勿关闭窗口…')
            def work():
                try:
                    self.events.put(('clean', execute(plan, f'TRASH {plan.id}', audit_path)))
                except Exception as error:
                    self.events.put(('error', str(error)))
            threading.Thread(target=work, daemon=True).start()
        button.configure(command=approved)
        button.pack(side='right', padx=20, pady=14)
        ttk.Button(dialog, text='取消', command=dialog.destroy).pack(side='right', pady=14)

    def save_report(self):
        if not self.result:
            return
        file = filedialog.asksaveasfilename(title='导出报告：包含本机文件路径，请谨慎分享', initialdir=str(Path.home() if self.result.mode == 'computer' else self.result.root), initialfile='clearguard-report.json', defaultextension='.json')
        if file:
            try:
                with Path(file).open('x', encoding='utf-8') as report:
                    report.write(json.dumps(asdict(self.result), default=str, ensure_ascii=False, indent=2))
            except OSError as error:
                messagebox.showerror('导出失败', str(error))

    def close(self):
        if self.cleaning:
            messagebox.showinfo('正在清理', '请等待本次清理结束，审计日志写入完成后再关闭。')
            return
        self.cancel.set()
        self.root.destroy()


def main(smoke=False):
    root = tk.Tk()
    app = App(root)
    if smoke:
        root.update()
        assert not app.chosen and app.result is None
        assert str(app.clear['state']) == 'disabled'
        assert str(app.computer['text']) == '全机扫描'
        assert str(app.computer['state']) == 'normal'
        root.destroy()
    else:
        root.mainloop()


if __name__ == '__main__':
    main()
