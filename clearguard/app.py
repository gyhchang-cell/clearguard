"""Native desktop UI. All filesystem work runs outside the Tk main thread."""
from __future__ import annotations
import json
import os
import sys
import queue
import threading
import tkinter as tk
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .presentation import (
    ACCENT, BACKGROUND, PANEL, SELECTION, SURFACE, TEXT, configure_theme,
)

from .core import (
    Progress, cleanup_candidates, execute, make_plan, scan, scan_computer,
)


def human(value):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1000:
            return f'{value:,.0f} {unit}' if unit == 'B' else f'{value:,.1f} {unit}'
        value /= 1000
    return f'{value:,.1f} PB'


def audit_directory():
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Logs' / 'ClearGuard'
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'ClearGuard' / 'Logs'
    return Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local' / 'state') / 'clearguard' / 'logs'



class App:
    def __init__(self, root):
        self.root = root
        self.root.title('ClearGuard')
        self.root.geometry('1080x670')
        self.root.minsize(830, 500)
        self.root.protocol('WM_DELETE_WINDOW', self.close)
        self.events = queue.Queue()
        self.cancel = threading.Event()
        self.result = None
        self.busy = False
        self.cleaning = False
        self.chosen = set()
        self.rows = {}
        self.eligible = {}
        self.path_groups = {}
        self.selected_counts = Counter()
        self.all_checked = tk.BooleanVar(value=False)
        self.page = 0
        self.folder = tk.StringVar()
        self.scope = tk.StringVar(value='尚未扫描')
        self.status = tk.StringVar()
        self.stats = tk.StringVar()
        self.query = tk.StringVar()
        self.filtered_groups = []
        self.deferred_groups = {}
        self.review_page = 0
        self.search_timer = None
        self.summary = tk.StringVar()
        self.size_summary = tk.StringVar()
        self.cleanup_hint = tk.StringVar()
        configure_theme(root)
        outer = ttk.Frame(root, padding=(20, 16))
        outer.pack(fill='both', expand=True)
        toolbar = ttk.Frame(outer)
        toolbar.pack(fill='x')
        ttk.Label(toolbar, text='ClearGuard', style='Title.TLabel').pack(side='left')
        self.stop = ttk.Button(toolbar, text='取消扫描', command=self.cancel_scan, state='disabled')
        self.stop.pack(side='right')
        self.start = ttk.Button(toolbar, text='扫描文件夹…', command=self.choose)
        self.start.pack(side='right', padx=(0, 8))
        self.computer = ttk.Button(toolbar, text='全机扫描', style='Primary.TButton',
                                   command=lambda: self.begin(whole_machine=True))
        self.computer.pack(side='right', padx=(0, 8))
        scan_status = ttk.Frame(outer)
        scan_status.pack(fill='x', pady=(12, 6))
        ttk.Label(scan_status, textvariable=self.scope, style='Muted.TLabel').pack(side='left')
        ttk.Label(scan_status, textvariable=self.status, style='Muted.TLabel').pack(side='right')
        self.progress = ttk.Progressbar(outer, mode='determinate', maximum=100,
                                       style='Scan.Horizontal.TProgressbar')
        self.progress.pack(fill='x')
        summary = ttk.Frame(outer)
        summary.pack(fill='x', pady=(9, 8))
        self.summary_label = ttk.Label(summary, textvariable=self.summary, style='Summary.TLabel')
        self.size_label = ttk.Label(summary, textvariable=self.size_summary, style='Muted.TLabel')
        notebook = self.notebook = ttk.Notebook(outer)
        notebook.pack(fill='both', expand=True)
        dup = ttk.Frame(notebook, padding=(5, 8))
        review = ttk.Frame(notebook, padding=(5, 8))
        details = ttk.Frame(notebook, padding=(5, 8))
        notebook.add(dup, text='内容相同的文件')
        notebook.add(review, text='大文件与旧文件')
        notebook.add(details, text='扫描范围与跳过项')
        filters = ttk.Frame(dup)
        filters.pack(fill='x', pady=(0, 7))
        ttk.Label(filters, text='按文件名或路径筛选', style='Muted.TLabel').pack(side='left', padx=(2, 8))
        self.search = ttk.Entry(filters, textvariable=self.query, width=33)
        self.search.pack(side='left')
        self.search.bind('<Escape>', lambda _: self.query.set(''))
        ttk.Label(filters, textvariable=self.stats, style='Muted.TLabel').pack(side='right')
        self.select_all = ttk.Checkbutton(filters, text='全选这些组的副本', variable=self.all_checked,
                                          command=self.toggle_all, state='disabled')
        self.select_all.pack(side='left', padx=(14, 0))
        self.query.trace_add('write', self.schedule_filter)
        self.tree = self.table(dup, ('choice', 'size', 'path'), ('清理状态', '大小', '文件位置'))
        self.tree.column('#0', width=190)
        self.tree.column('choice', width=174, stretch=False)
        self.tree.column('size', width=110, stretch=False, anchor='e')
        self.tree.column('path', width=580, minwidth=180)
        self.tree.tag_configure('group', background=SURFACE, foreground=TEXT)
        self.tree.tag_configure('eligible', foreground=ACCENT)
        self.tree.bind('<Button-1>', self.click_choice)
        self.tree.bind('<space>', self.toggle)
        self.tree.bind('<<TreeviewOpen>>', self.expand_group)
        pager = ttk.Frame(dup)
        pager.pack(side='bottom', before=self.tree.master, fill='x', pady=(6, 0))
        self.page_label = tk.StringVar()
        self.next_page = ttk.Button(pager, text='下一页', command=lambda: self.change_page(1), state='disabled')
        self.next_page.pack(side='right')
        ttk.Label(pager, textvariable=self.page_label).pack(side='right', padx=8)
        self.prev_page = ttk.Button(pager, text='上一页', command=lambda: self.change_page(-1), state='disabled')
        self.prev_page.pack(side='right')
        ttk.Label(review, text='列出较大或较久未修改的文件，方便查看。此页不能勾选清理。',
                  style='Muted.TLabel').pack(anchor='w', pady=(0, 7))
        self.review = self.table(review, ('size', 'path'), ('大小', '路径'))
        self.review.column('#0', width=160)
        self.review.column('size', width=110, stretch=False)
        self.review.column('path', width=650)
        review_pager = ttk.Frame(review)
        review_pager.pack(side='bottom', before=self.review.master, fill='x', pady=(6, 0))
        self.review_next = ttk.Button(review_pager, text='下一页', command=lambda: self.change_review(1), state='disabled')
        self.review_next.pack(side='right')
        self.review_counter = ttk.Label(review_pager, text='', style='Muted.TLabel')
        self.review_counter.pack(side='right', padx=8)
        self.review_prev = ttk.Button(review_pager, text='上一页', command=lambda: self.change_review(-1), state='disabled')
        self.review_prev.pack(side='right')
        self.review_tab = review
        self.notebook.bind('<<NotebookTabChanged>>', self.tab_changed)
        self.notes = tk.Text(details, wrap='word', height=12, padx=12, pady=10,
                             bg=PANEL, fg=TEXT, insertbackground=ACCENT,
                             selectbackground=SELECTION, relief='flat', highlightthickness=0)
        self.notes.pack(fill='both', expand=True)
        self.write_notes('')
        footer = ttk.Frame(outer)
        footer.pack(side='bottom', before=notebook, fill='x', pady=(12, 0))
        self.hint_label = ttk.Label(footer, textvariable=self.cleanup_hint, style='Muted.TLabel')
        controls = self.cleanup_controls = ttk.Frame(footer)
        controls.pack(fill='x')
        self.export = ttk.Button(controls, text='保存扫描报告…', command=self.save_report, state='disabled')
        self.export.pack(side='left')
        self.clear = ttk.Button(controls, text='一键清理…', style='Primary.TButton',
                                command=self.confirm, state='disabled')
        self.clear.pack(side='right')
        self.selection_label = ttk.Label(controls, text='')
        self.selection_label.pack(side='right', padx=16)
        root.bind('<Command-f>' if root.tk.call('tk', 'windowingsystem') == 'aqua' else '<Control-f>',
                  lambda _: self.search.focus_set())
        self.root.after(80, self.poll)

    def cancel_scan(self):
        self.cancel.set()
        self.status.set('正在取消…')
        self.stop.configure(state='disabled')

    def schedule_filter(self, *_):
        if self.search_timer is not None:
            self.root.after_cancel(self.search_timer)
        self.search_timer = self.root.after(130, self.apply_filter)

    def apply_filter(self, reset_page=True):
        if self.search_timer is not None:
            self.root.after_cancel(self.search_timer)
            self.search_timer = None
        if not self.result:
            return
        terms = self.query.get().strip().casefold().split()
        groups = self.result.groups
        self.filtered_groups = (
            [group for group in groups
             if any(all(term in path for term in terms)
                    for path in (str(item.path).casefold() for item in group.files))]
            if terms else groups
        )
        self.stats.set(f'找到 {len(self.filtered_groups):,} 组' if terms else '')
        if reset_page:
            self.page = 0
        self.render_groups()
        self.update_bulk_control()

    def table(self, parent, columns, headings):
        frame = ttk.Frame(parent)
        frame.pack(fill='both', expand=True)
        tree = ttk.Treeview(frame, columns=columns, show='tree headings', selectmode='browse', style='Results.Treeview')
        tree.heading('#0', text='文件名', anchor='w')
        for c, title in zip(columns, headings):
            tree.heading(c, text=title, anchor='e' if c == 'size' else 'w')
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
            self.begin()

    def click_choice(self, event):
        if self.tree.identify_column(event.x) == '#1':
            row = self.tree.identify_row(event.y)
            if row in self.rows:
                self.tree.selection_set(row)
                self.toggle()
                return 'break'
        return None

    def lock(self, busy, cleaning=False):
        self.busy, self.cleaning = busy, cleaning
        for control in (self.start, self.computer, self.search):
            control.configure(state='disabled' if busy else 'normal')
        self.stop.configure(state='normal' if busy and not cleaning else 'disabled')
        self.export.configure(state='normal' if self.result and not busy else 'disabled')
        self.clear.configure(state='normal' if self.chosen and not busy else 'disabled')
        self.update_bulk_control()
        if busy:
            self.progress.configure(mode='determinate', value=0)

    def begin(self, whole_machine=False):
        if self.busy:
            return
        if not whole_machine and not self.folder.get().strip():
            return self.choose()
        self.result = None
        self.eligible.clear()
        self.path_groups.clear()
        self.selected_counts.clear()
        self.page = 0
        self.reset_pager()
        self.stats.set('')
        self.query.set('')
        self.filtered_groups = []
        self.deferred_groups.clear()
        self.review_page = 0
        self.change_review(0)
        self.clear_summary()
        self.chosen.clear()
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        self.review.delete(*self.review.get_children())
        self.update_selection()
        self.cancel.clear()
        self.latest_progress = self.displayed_progress = None
        self.lock(True)
        self.status.set('正在发现本地磁盘…' if whole_machine else '正在扫描…')
        folder = Path(self.folder.get()).expanduser()
        self.scope.set('全机扫描' if whole_machine else folder.name)
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
        event = getattr(self, 'latest_progress', None)
        if self.busy and not self.cleaning and not self.cancel.is_set() and event is not None and event is not getattr(self, 'displayed_progress', None):
            self.displayed_progress = event
            if isinstance(event, Progress):
                value = 100 * event.completed / event.total if event.total else 0
                self.progress.configure(value=value)
                if event.phase == 'discover':
                    label = f'已发现 {event.completed:,} 个文件' if event.completed else '正在扫描目录'
                elif event.read_bytes is not None:
                    label = f'{event} · {int(value)}% · 已读 {human(event.read_bytes)}'
                elif event.phase == 'done':
                    label = '扫描完成'
                elif event.total:
                    phase = '筛选候选' if event.phase == 'sample' else '逐字节复核'
                    label = f'{phase} · {event.completed:,}/{event.total:,}'
                else:
                    label = str(event)
                self.status.set(label)
            else:
                self.status.set(str(event))
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == 'scan':
                    self.latest_progress = None
                    self.result = payload
                    self.populate()
                    self.lock(False)
                    self.progress.configure(value=0 if payload.cancelled else 100)
                elif kind == 'plan':
                    self.lock(False)
                    self.progress.configure(value=100)
                    self.status.set('扫描完成')
                    self.show_plan(payload)
                elif kind == 'error':
                    self.lock(False)
                    self.status.set('操作未完成：' + payload)
                    messagebox.showerror('操作停止', payload)
                elif kind == 'clean':
                    payload, audit_path = payload
                    success = sum(x['status'] == 'trashed' for x in payload)
                    self.status.set(f'已送入系统废纸篓/回收站：{success} 个。扫描结果已失效，请重新扫描。')
                    self.result = None
                    self.chosen.clear()
                    self.rows.clear()
                    self.eligible.clear()
                    self.path_groups.clear()
                    self.selected_counts.clear()
                    self.reset_pager()
                    self.change_review(0)
                    self.deferred_groups.clear()
                    self.filtered_groups = []
                    self.clear_summary()
                    self.progress.configure(value=0)
                    self.tree.delete(*self.tree.get_children())
                    self.review.delete(*self.review.get_children())
                    self.update_selection()
                    self.lock(False)
                    failures = [x.get('error', '') for x in payload if x['status'] == 'failed']
                    messagebox.showinfo('清理结果', f'已送入回收站 {success} 个文件。\n可通过系统回收站恢复。\n移动到回收站不会立即释放磁盘空间。' + ('\n失败后已停止：' + '\n'.join(failures) if failures else '')
                                        + f'\n\n清理记录：{audit_path}')
        except queue.Empty:
            pass
        self.root.after(80, self.poll)

    GROUPS_PER_PAGE = 200

    def reset_pager(self):
        self.page_label.set('')
        self.prev_page.configure(state='disabled')
        self.next_page.configure(state='disabled')

    def change_page(self, delta):
        if self.busy or not self.result:
            return
        page_count = (len(self.filtered_groups) + self.GROUPS_PER_PAGE - 1) // self.GROUPS_PER_PAGE
        if 0 <= self.page + delta < page_count:
            self.page += delta
            self.render_groups()

    def render_groups(self):
        groups = self.filtered_groups
        self.deferred_groups.clear()
        self.rows.clear()
        self.tree.delete(*self.tree.get_children())
        start = self.page * self.GROUPS_PER_PAGE
        for group in groups[start:start + self.GROUPS_PER_PAGE]:
            parent = self.tree.insert('', 'end', text=group.files[0].path.name,
                                      values=(f'{len(group.files)} 份内容相同', f'每份 {human(group.files[0].size)}', ''),
                                      tags=('group',), open=len(group.files) <= 6)
            if len(group.files) > 6:
                self.deferred_groups[parent] = group
                self.tree.insert(parent, 'end')  # disclosure cue, replaced on open
            else:
                self.render_files(parent, group)
        self.tree.yview_moveto(0)
        if not groups:
            self.reset_pager()
            return
        page_count = (len(groups) + self.GROUPS_PER_PAGE - 1) // self.GROUPS_PER_PAGE
        self.page_label.set(f'第 {self.page + 1} 页／共 {page_count} 页' if page_count > 1 else '')
        self.prev_page.configure(state='normal' if self.page else 'disabled')
        self.next_page.configure(state='normal' if self.page + 1 < page_count else 'disabled')

    def expand_group(self, _event):
        parent = self.tree.focus()
        group = self.deferred_groups.pop(parent, None)
        if group is not None:
            self.tree.delete(*self.tree.get_children(parent))
            self.render_files(parent, group)

    def render_files(self, parent, group):
        for item in group.files:
            choice = self.choice_label(item.path)
            row = self.tree.insert(parent, 'end', text=item.path.name,
                                   values=(choice, human(item.size), str(item.path)),
                                   tags=('eligible',) if item.path in self.eligible else ())
            if item.path in self.eligible:
                self.rows[row] = item

    REVIEW_PER_PAGE = 400

    def tab_changed(self, _event):
        if self.notebook.select() == str(self.review_tab):
            self.change_review(0)

    def change_review(self, delta):
        files = self.result.review if self.result else ()
        page_count = (len(files) + self.REVIEW_PER_PAGE - 1) // self.REVIEW_PER_PAGE
        self.review_page = min(max(0, self.review_page + delta), max(0, page_count - 1))
        self.review.delete(*self.review.get_children())
        start = self.review_page * self.REVIEW_PER_PAGE
        for item in files[start:start + self.REVIEW_PER_PAGE]:
            self.review.insert('', 'end', text=item.path.name, values=(human(item.size), str(item.path)))
        self.review.yview_moveto(0)
        self.review_counter.configure(text=f'第 {self.review_page + 1} 页／共 {page_count} 页' if page_count > 1 else '')
        self.review_prev.configure(state='normal' if self.review_page else 'disabled')
        self.review_next.configure(state='normal' if self.review_page + 1 < page_count else 'disabled')

    def populate(self):
        r = self.result
        self.eligible = cleanup_candidates(r)
        self.path_groups = {item.path: group for group in r.groups for item in group.files}
        self.apply_filter(reset_page=False)
        self.review.delete(*self.review.get_children())
        self.summary.set(f'已检查 {r.scanned:,} 个文件' if r.cancelled else
                         f'已检查 {r.scanned:,} 个文件，找到 {len(r.groups):,} 组内容相同的文件')
        self.summary_label.pack(anchor='w')
        if r.groups:
            self.size_summary.set(f'每组留一份，其余副本合计 {human(r.potential)}。实际可释放空间可能更少。')
            self.size_label.pack(anchor='w', pady=(3, 0))
        else:
            self.size_label.pack_forget()
        if self.notebook.select() == str(self.review_tab):
            self.change_review(0)
        details = ['开始扫描的位置', *(str(p) for p in (r.roots or (r.root,))), '',
                   f'因保护规则跳过 {r.skipped:,} 个文件或文件夹', *r.excluded, '',
                   f'扫描遇到 {len(r.errors):,} 个问题', *r.errors]
        self.write_notes('\n'.join(details))
        self.status.set('已取消，不能用本次结果清理' if r.cancelled else
                        ('扫描完成，有未能检查的项目' if r.errors else '扫描完成'))
        self.update_selection()

    def clear_summary(self):
        self.summary.set('')
        self.size_summary.set('')
        self.summary_label.pack_forget()
        self.size_label.pack_forget()

    def choice_label(self, path):
        if path in self.chosen:
            return '☑ 已勾选'
        group = self.path_groups[path]
        if self.selected_counts[group.id] == len(group.files) - 1:
            return '保留这份'
        return '□ 可勾选'

    def toggle(self, event=None):
        if self.busy:
            return
        selection = self.tree.selection()
        if not selection:
            return
        row = selection[0]
        item = self.rows.get(row)
        if item is None:
            return
        group = self.path_groups[item.path]
        if item.path in self.chosen:
            self.chosen.remove(item.path)
            self.selected_counts[group.id] -= 1
        elif self.selected_counts[group.id] < len(group.files) - 1:
            self.chosen.add(item.path)
            self.selected_counts[group.id] += 1
        for sibling in self.tree.get_children(self.tree.parent(row)):
            if sibling in self.rows:
                self.tree.set(sibling, 'choice', self.choice_label(self.rows[sibling].path))
        self.update_selection()

    def update_bulk_control(self):
        groups = self.filtered_groups
        all_selected = bool(groups) and all(
            self.selected_counts[g.id] == len(g.files) - 1 for g in groups)
        self.all_checked.set(all_selected)
        self.select_all.configure(state='normal' if groups and not self.busy else 'disabled')
        partial = not all_selected and any(self.selected_counts[g.id] for g in groups)
        self.select_all.state(['alternate'] if partial else ['!alternate'])

    def toggle_all(self):
        if self.busy:
            return
        select = self.all_checked.get()
        if self.search_timer is not None:
            self.apply_filter()
            select = not self.all_checked.get()
        if not self.filtered_groups:
            return
        for group in self.filtered_groups:
            remaining = [item.path for item in group.files if item.path not in self.chosen]
            if select:
                # Preserve one already unselected file and any manual choices.
                self.chosen.update(remaining[1:])
                self.selected_counts[group.id] = len(group.files) - 1
            else:
                self.chosen.difference_update(item.path for item in group.files)
                self.selected_counts.pop(group.id, None)
        for row, item in self.rows.items():
            self.tree.set(row, 'choice', self.choice_label(item.path))
        self.update_selection()

    def update_selection(self):
        size = sum(self.eligible[path].size for path in self.chosen)
        self.selection_label.configure(text=f'已选 {len(self.chosen)} 个 · {human(size)}' if self.chosen else '')
        self.clear.configure(state='normal' if self.chosen and not self.busy else 'disabled')
        self.update_bulk_control()
        if not self.result or self.result.cancelled or not self.result.groups:
            hint = ''
        elif self.chosen:
            hint = '点「一键清理」查看清单。确认后才会移入回收站。'
        else:
            hint = '点文件旁的方框即可勾选。全选涵盖筛选结果的所有页，每组留一份。'
        self.cleanup_hint.set(hint)
        if hint:
            self.hint_label.pack(before=self.cleanup_controls, anchor='w', pady=(0, 6))
        else:
            self.hint_label.pack_forget()

    def write_notes(self, extra):
        self.notes.configure(state='normal')
        self.notes.delete('1.0', 'end')
        self.notes.insert('1.0', extra)
        self.notes.configure(state='disabled')

    def confirm(self):
        if self.busy or not self.chosen or self.result is None:
            return
        result, selected = self.result, list(self.chosen)
        self.lock(True, cleaning=True)
        self.status.set('正在核对已勾选的文件…')

        def work():
            try:
                self.events.put(('plan', make_plan(result, selected, skip_blocked=True)))
            except Exception as error:
                self.events.put(('error', str(error)))

        threading.Thread(target=work, daemon=True).start()

    def show_plan(self, plan):
        dialog = tk.Toplevel(self.root)
        dialog.title('查看清理清单')
        dialog.configure(bg=BACKGROUND)
        dialog.geometry('820x520')
        dialog.transient(self.root)
        dialog.grab_set()
        count = len(plan.targets)
        title = f'{count:,} 个文件待移入回收站 · {human(plan.bytes)}' if count else '没有可移动的文件'
        if plan.blocked:
            title += f' · 保留 {len(plan.blocked):,} 个异常项'
        ttk.Label(dialog, text=title, font=('TkDefaultFont', 15, 'bold'), wraplength=740).pack(padx=20, pady=16)
        frame = ttk.Frame(dialog)
        frame.pack(fill='both', expand=True, padx=20)
        text = tk.Text(frame, wrap='word', bg=PANEL, fg=TEXT, padx=12, pady=8,
                       insertbackground=ACCENT, selectbackground=SELECTION, relief='flat')
        scroll = ttk.Scrollbar(frame, orient='vertical', command=text.yview)
        scroll.pack(side='right', fill='y')
        text.configure(yscrollcommand=scroll.set)
        text.pack(side='left', fill='both', expand=True)
        targets = {f.path for f in plan.targets}
        sections = []
        if plan.blocked:
            sections.extend(['会保留的异常项', *(f'{path}\n  {reason}' for path, reason in plan.blocked), ''])
        for group in plan.groups:
            sections.extend(['保留', *(str(f.path) for f in group.files if f.path not in targets),
                             '移入回收站', *(str(f.path) for f in group.files if f.path in targets), ''])
        text.insert('1.0', '\n'.join(sections))
        text.configure(state='disabled')
        consent = tk.BooleanVar(value=False)
        button = ttk.Button(dialog, text='确认移入回收站', state='disabled')
        if count:
            ttk.Checkbutton(dialog, text='我已核对文件位置，确认勾选的是不再需要的副本。', variable=consent,
                            command=lambda: button.configure(state='normal' if consent.get() else 'disabled')).pack(pady=12)
            ttk.Label(dialog, text='文件可从回收站恢复。清理记录自动保存在本机。').pack()
        def approved():
            if not count or not consent.get():
                return
            audit_path = audit_directory() / f'clearguard-{plan.id}.jsonl'
            dialog.destroy()
            self.lock(True, cleaning=True)
            self.status.set('正在复核并移动所选文件，请勿关闭窗口…')
            def work():
                try:
                    outcomes = execute(plan, f'TRASH {plan.id}', audit_path)
                    self.events.put(('clean', (outcomes, audit_path)))
                except Exception as error:
                    self.events.put(('error', str(error)))
            threading.Thread(target=work, daemon=True).start()
        button.configure(command=approved)
        if count:
            button.pack(side='right', padx=20, pady=14)
        ttk.Button(dialog, text='取消' if count else '关闭', command=dialog.destroy).pack(side='right', padx=14, pady=14)

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
