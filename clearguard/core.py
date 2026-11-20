"""Conservative, offline duplicate detection and explicit-consent cleanup."""
from __future__ import annotations

import hashlib
import os
import sys
import stat
import threading
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

CHUNK = 1024 * 1024
BLOCKED_NAMES = {
    '.git', '.svn', '.hg', '.ssh', '.gnupg', '.aws', '.azure', '.config',
    '.trash', '.trashes', '$recycle.bin', 'system volume information',
    'windows', 'program files', 'program files (x86)', 'programdata',
    '.spotlight-v100', '.fseventsd', '.documentrevisions-v100', '.temporaryitems',
    'recovery', 'config.msi', 'pagefile.sys', 'swapfile.sys', 'hiberfil.sys',
    'bootmgr', 'bootnxt', '$winreagent', '$windows.~bt', '$windows.~ws', 'appdata', 'library', 'applications', 'system', 'node_modules',
    '.venv', 'venv', '__pycache__', 'onedrive', 'dropbox',
}
PACKAGE_SUFFIXES = {'.app', '.bundle', '.framework', '.photoslibrary', '.photolibrary',
                    '.sparsebundle', '.backupbundle', '.git', '.lproj'}
PROJECT_MARKERS = {'.git', 'pyproject.toml', 'package.json', 'Cargo.toml',
                   'pom.xml', 'build.gradle', 'build.gradle.kts', 'go.mod',
                   'CMakeLists.txt', 'settings.gradle', 'settings.gradle.kts'}
SYSTEM_ROOTS = ('/System', '/Library', '/Applications', '/usr', '/bin', '/sbin',
                '/etc', '/private', '/dev', '/proc', '/sys', '/run', '/boot')


class UnsafeFile(ValueError):
    pass


class Cancelled(Exception):
    pass


def checkpoint(cancel: threading.Event | None):
    if cancel and cancel.is_set():
        raise Cancelled()


def assert_safe(path: Path):
    """Reject links, junctions, cloud placeholders and protected ancestors."""
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', 0):
            raise UnsafeFile('链接、联接或云占位文件被保护')
        # UF_COMPRESSED is not a placeholder; UF_DATALESS is.
        if getattr(info, 'st_flags', 0) & 0x40000000:
            raise UnsafeFile('未下载的云文件被保护')
        if (part.name.casefold() in BLOCKED_NAMES or part.suffix.casefold() in PACKAGE_SUFFIXES
                or part.name.casefold().startswith('onedrive - ')):
            raise UnsafeFile('系统、应用、云同步或受保护目录')
        if part.is_dir() and any((part / marker).exists() for marker in PROJECT_MARKERS):
            raise UnsafeFile('开发项目受保护')
    if os.name != 'nt' and any(path == Path(p) or Path(p) in path.parents for p in SYSTEM_ROOTS):
        raise UnsafeFile('系统目录受保护')
    return path


@dataclass(frozen=True)
class ComputerScope:
    roots: tuple[Path, ...]
    boundaries: tuple[Path, ...]
    excluded: tuple[str, ...]


def discover_computer_scope():
    """Read mount metadata only; never connect to network shares or mount disks."""
    import psutil
    local_types = {'apfs', 'hfs', 'hfs+', 'ntfs', 'refs', 'exfat', 'msdos',
                   'fat', 'fat32', 'vfat', 'ext2', 'ext3', 'ext4', 'xfs', 'btrfs', 'zfs'}
    partitions = psutil.disk_partitions(all=True)
    boundaries = set()
    roots = set()
    excluded = []
    if sys.platform == 'darwin':
        # The visible /Users tree uses APFS firmlinks into the Data volume.
        # Walking /System/Volumes/Data as well would count those files twice.
        roots.add(Path('/'))
    for disk in partitions:
        path = Path(os.path.abspath(disk.mountpoint))
        boundaries.add(path)
        options = set(disk.opts.casefold().split(','))
        if (disk.fstype.casefold() not in local_types or
                options & {'remote', 'cdrom', 'unmounted', 'ramdisk'} or
                disk.mountpoint.startswith('\\\\')):
            excluded.append(f'{path}：网络、虚拟、光盘或不支持的文件系统，未扫描')
            continue
        if sys.platform == 'darwin' and path != Path('/'):
            if path.parent != Path('/Volumes'):
                excluded.append(f'{path}：系统内部卷或重复挂载，未单独扫描')
                continue
        roots.add(path)
    if not roots:
        raise UnsafeFile('未发现可扫描的本地磁盘，请使用文件夹扫描')
    return ComputerScope(tuple(sorted(roots, key=str)), tuple(sorted(boundaries, key=str)), tuple(excluded))


def scan_computer(cancel=None, progress=lambda _: None):
    if cancel and cancel.is_set():
        return ScanResult(Path.home(), cancelled=True, mode='computer')
    progress('正在发现本地磁盘…')
    scope = discover_computer_scope()
    return _scan_roots(scope.roots, cancel, progress, mode='computer',
                       boundaries=set(scope.boundaries), excluded=list(scope.excluded))


@dataclass(frozen=True)
class Snapshot:
    path: Path
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    links: int

    @classmethod
    def read(cls, path: Path):
        path = assert_safe(path)
        s = path.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
            raise UnsafeFile('非普通文件或硬链接被保护')
        return cls(path, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_dev, s.st_ino, s.st_nlink)

    def verify(self):
        if Snapshot.read(self.path) != self:
            raise UnsafeFile('文件在扫描后发生变化，请重新扫描')


def digest(item: Snapshot, cancel=None):
    item.verify()
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(item.path, flags), 'rb') as stream:
        s = os.fstat(stream.fileno())
        if (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) != (
                item.device, item.inode, item.size, item.mtime_ns, item.ctime_ns):
            raise UnsafeFile('打开的文件已变化')
        h = hashlib.sha256()
        while True:
            checkpoint(cancel)
            block = stream.read(CHUNK)
            if not block:
                break
            h.update(block)
    item.verify()
    return h.hexdigest()


def equal(a: Snapshot, b: Snapshot, cancel=None):
    a.verify()
    b.verify()
    flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(a.path, flags), 'rb') as left, os.fdopen(os.open(b.path, flags), 'rb') as right:
        while True:
            checkpoint(cancel)
            x, y = left.read(CHUNK), right.read(CHUNK)
            if x != y:
                return False
            if not x:
                break
    a.verify()
    b.verify()
    return True


@dataclass(frozen=True)
class DuplicateGroup:
    id: str
    sha256: str
    files: tuple[Snapshot, ...]


@dataclass
class ScanResult:
    root: Path
    groups: list[DuplicateGroup] = field(default_factory=list)
    review: list[Snapshot] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: int = 0
    scanned: int = 0
    bytes_seen: int = 0
    cancelled: bool = False
    roots: tuple[Path, ...] = ()
    mode: str = 'folder'
    excluded: list[str] = field(default_factory=list)

    @property
    def potential(self):
        return sum(g.files[0].size * (len(g.files) - 1) for g in self.groups)


def scan(root: Path, cancel=None, progress: Callable[[str], None] = lambda _: None,
         large_bytes=100 * 1024 * 1024, old_days=180):
    root = assert_safe(Path(root).absolute())
    if not root.is_dir() or root == Path(root.anchor):
        raise UnsafeFile('请选择个人文件夹，不扫描整个系统根目录')
    return _scan_roots((root,), cancel, progress, large_bytes, old_days)


def _scan_roots(roots, cancel=None, progress=lambda _: None,
                large_bytes=100 * 1024 * 1024, old_days=180, *, mode='folder',
                boundaries=None, excluded=None):
    """One global size/hash index, including duplicates across different disks."""
    roots = tuple(dict.fromkeys(Path(p).absolute() for p in roots))
    if not roots:
        raise UnsafeFile('没有扫描位置')
    boundaries = boundaries or set()
    result = ScanResult(roots[0], roots=roots, mode=mode, excluded=excluded or [])
    sizes = defaultdict(list)
    seen = set()
    threshold = time.time_ns() - old_days * 86400 * 10**9

    def walk_error(error):
        result.errors.append(str(error))

    try:
        for root in roots:
            checkpoint(cancel)
            try:
                assert_safe(root)
                if not root.is_dir():
                    raise UnsafeFile('磁盘未挂载或目录不可用')
            except (OSError, UnsafeFile) as error:
                result.errors.append(f'{root}：{error}')
                continue
            for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
                checkpoint(cancel)
                progress(f'正在遍历 · {directory}')
                kept = []
                for name in sorted(dirs):
                    child = Path(directory) / name
                    if child in boundaries or (mode == 'computer' and os.path.ismount(child)):
                        if child not in roots:
                            result.excluded.append(f'{child}：独立挂载点，未从当前磁盘进入')
                        continue
                    try:
                        assert_safe(child)
                        kept.append(name)
                    except UnsafeFile as error:
                        result.skipped += 1
                        result.excluded.append(f'{child}：{error}')
                    except OSError as error:
                        result.skipped += 1
                        result.errors.append(f'{child}：{error}')
                dirs[:] = kept
                for name in sorted(names):
                    checkpoint(cancel)
                    try:
                        item = Snapshot.read(Path(directory) / name)
                        identity = (item.device, item.inode)
                        if identity in seen:
                            continue
                        seen.add(identity)
                        result.scanned += 1
                        result.bytes_seen += item.size
                        if item.size:
                            sizes[item.size].append(item)
                        if item.size >= large_bytes or item.mtime_ns < threshold:
                            result.review.append(item)
                        if result.scanned % 100 == 0:
                            progress(f'已读取 {result.scanned:,} 个文件…')
                    except UnsafeFile:
                        result.skipped += 1
                    except OSError as error:
                        result.errors.append(str(error))
        for size, items in sizes.items():
            checkpoint(cancel)
            if len(items) < 2:
                continue
            hashes = defaultdict(list)
            for item in items:
                try:
                    progress(f'校验内容 · {item.path.name}')
                    hashes[digest(item, cancel)].append(item)
                except (OSError, UnsafeFile) as error:
                    result.errors.append(f'{item.path}: {error}')
            for sha, matches in hashes.items():
                # Partition by byte equality as an independent hash-collision defense.
                partitions = []
                for item in matches:
                    try:
                        for group in partitions:
                            if equal(group[0], item, cancel):
                                group.append(item)
                                break
                        else:
                            partitions.append([item])
                    except (OSError, UnsafeFile) as error:
                        result.errors.append(f'{item.path}: {error}')
                for group in partitions:
                    if len(group) > 1:
                        result.groups.append(DuplicateGroup(uuid.uuid4().hex, sha, tuple(group)))
        result.groups.sort(key=lambda g: g.files[0].size * (len(g.files)-1), reverse=True)
        result.review.sort(key=lambda f: f.size, reverse=True)
    except Cancelled:
        result.cancelled = True
        result.groups.clear()  # incomplete scans never authorize cleanup
    return result


@dataclass(frozen=True)
class Plan:
    id: str
    groups: tuple[DuplicateGroup, ...]
    targets: tuple[Snapshot, ...]
    created: float

    @property
    def bytes(self):
        return sum(f.size for f in self.targets)


def make_plan(result: ScanResult, selected: list[Path]):
    if result.cancelled:
        raise UnsafeFile('扫描未完成')
    wanted = {Path(p) for p in selected}
    if not wanted:
        raise UnsafeFile('请先选择文件')
    known = {f.path: f for g in result.groups for f in g.files}
    if wanted - known.keys():
        raise UnsafeFile('只能清理本次扫描核验的重复文件')
    groups = tuple(g for g in result.groups if any(f.path in wanted for f in g.files))
    for g in groups:
        if all(f.path in wanted for f in g.files):
            raise UnsafeFile('每组必须至少保留一份文件')
    return Plan(uuid.uuid4().hex, groups, tuple(known[p] for p in sorted(wanted)), time.monotonic())


def execute(plan: Plan, consent: str, audit: Path, trash=None):
    """Only caller-held, reviewed plans + matching explicit consent are accepted.

    This is an in-process consent guard, not a hostile-code security boundary.
    """
    import json
    if consent != f'TRASH {plan.id}':
        raise UnsafeFile('未取得本次清理的明确确认')
    if time.monotonic() - plan.created > 600:
        raise UnsafeFile('确认已过期，请重新选择')
    if trash is None:
        from send2trash import send2trash
        trash = send2trash
    targets = {f.path for f in plan.targets}
    keepers = {}
    # Preflight ALL items before the first mutation.
    for group in plan.groups:
        keeper = next(f for f in group.files if f.path not in targets)
        keeper.verify()
        if digest(keeper) != group.sha256:
            raise UnsafeFile('保留文件内容已改变')
        for f in group.files:
            if f.path in targets:
                f.verify()
                if digest(f) != group.sha256 or not equal(keeper, f):
                    raise UnsafeFile('清理前内容复核失败')
                keepers[f.path] = keeper
    if targets != keepers.keys():
        raise UnsafeFile('清理计划不完整')
    audit = Path(audit)
    audit.parent.mkdir(parents=True, exist_ok=True)
    outcomes = []
    # Audit must be writable BEFORE any cleanup; append + fsync intent first.
    with audit.open('x', encoding='utf-8') as log:
        def record(entry):
            log.write(json.dumps(entry, ensure_ascii=False) + '\n')
            log.flush()
            os.fsync(log.fileno())
        for item in plan.targets:
            keeper = keepers[item.path]
            entry = {'time': time.time(), 'plan': plan.id, 'path': str(item.path),
                     'keeper': str(keeper.path), 'bytes': item.size}
            try:
                item.verify()
                keeper.verify()
                if not equal(item, keeper):
                    raise UnsafeFile('执行时内容不一致')
                record({**entry, 'status': 'intent'})
                trash(str(item.path))  # No unlink/rmtree fallback, ever.
            except (OSError, ValueError) as error:
                entry.update(status='failed', error=str(error))
            else:
                entry['status'] = 'trashed'
            record(entry)
            outcomes.append(entry)
            if entry['status'] == 'failed':
                break
    return outcomes
