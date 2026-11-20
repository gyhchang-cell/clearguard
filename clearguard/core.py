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
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

CHUNK = 1024 * 1024
BLOCKED_NAMES = {
    '.git', '.svn', '.hg', '.ssh', '.gnupg', '.aws', '.azure', '.config',
    '.codex', '.vscode', '.idea', '.minecraft', 'cloudstorage', 'mobile documents',
    '.cache', '.npm', '.rustup', '.cargo', '.gradle', '.m2', '.local',
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

MANAGED_WORKSPACES = (Path.home() / 'Documents' / 'Codex',)


class UnsafeFile(ValueError):
    pass


class Cancelled(Exception):
    pass


def checkpoint(cancel: threading.Event | None):
    if cancel and cancel.is_set():
        raise Cancelled()


def _check_entry(path, info):
    if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_reparse_tag', 0):
        raise UnsafeFile('链接、联接或云占位文件被保护')
    if getattr(info, 'st_flags', 0) & 0x40000000:
        raise UnsafeFile('未下载的云文件被保护')
    if (path.name.casefold() in BLOCKED_NAMES or path.suffix.casefold() in PACKAGE_SUFFIXES
            or path.name.casefold().startswith('onedrive - ')):
        raise UnsafeFile('系统、应用、云同步或受保护目录')


@lru_cache(maxsize=8192)
def _directory_rules(path, markers):
    """Cache path spelling and rule inputs; never cache file identity here."""
    name = path.name.casefold()
    restricted = (
        (os.name != 'nt' and str(path) in SYSTEM_ROOTS)
        or name in BLOCKED_NAMES or path.suffix.casefold() in PACKAGE_SUFFIXES
        or name.startswith('onedrive - ')
    )
    return restricted, tuple(str(path / marker) for marker in markers)


@lru_cache(maxsize=8192)
def _ancestor_chain(parent):
    return tuple(reversed((parent, *parent.parents)))


def _check_directory(path, info, checked=None, markers=None):
    if path.name.casefold() == 'codex' and any(
            str(path).casefold() == str(workspace).casefold()
            for workspace in MANAGED_WORKSPACES):
        raise UnsafeFile('Codex 工作区受保护')
    if not stat.S_ISDIR(info.st_mode):
        raise UnsafeFile('父路径不是文件夹')
    if getattr(info, 'st_reparse_tag', 0) or getattr(info, 'st_flags', 0) & 0x40000000:
        raise UnsafeFile('联接或云占位目录被保护')
    # Cache only project marker checks during read-only scanning. Each
    # ancestor identity and type is freshly obtained from lstat.
    stamp = (info.st_dev, info.st_ino, info.st_mtime_ns, info.st_ctime_ns)
    if checked is not None and checked.get(path) == stamp:
        return
    restricted, marker_paths = _directory_rules(path, markers or frozenset(PROJECT_MARKERS))
    if restricted:
        raise UnsafeFile('系统、应用、云同步或受保护目录')
    if any(os.path.exists(marker) for marker in marker_paths):
        raise UnsafeFile('开发项目受保护')
    if checked is not None and info.st_ino:
        if len(checked) >= 8192:
            checked.clear()
        checked[path] = stamp


def _validate_path(path: Path, checked=None):
    if path.parent != path:
        markers = frozenset(PROJECT_MARKERS)
        for part in _ancestor_chain(path.parent):
            _check_directory(part, part.lstat(), checked, markers)
    info = path.lstat()
    if stat.S_ISDIR(info.st_mode):
        _check_directory(path, info, checked)
    else:
        if os.name != 'nt' and str(path) in SYSTEM_ROOTS:
            raise UnsafeFile('系统目录受保护')
        _check_entry(path, info)
    return info


def assert_safe(path: Path):
    """Fresh checks for every ancestor before cleanup."""
    path = Path(os.path.abspath(path))
    _validate_path(path)
    return path


class Progress(str):
    """String-compatible event; totals are per phase, never guessed overall work."""
    def __new__(cls, text, phase, completed=0, total=None, unit='个', *, read_bytes=None):
        obj = super().__new__(cls, text)
        obj.phase, obj.completed, obj.total, obj.unit = phase, completed, total, unit
        obj.read_bytes = read_bytes
        return obj


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
    progress(Progress('正在发现本地磁盘…', 'discover'))
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
    birthtime_ns: int | None = None

    @classmethod
    def read(cls, path: Path):
        path = Path(os.path.abspath(path))
        return cls._from_info(path, _validate_path(path))

    @classmethod
    def _from_info(cls, path, s):
        _check_entry(path, s)
        if not stat.S_ISREG(s.st_mode) or s.st_nlink != 1:
            raise UnsafeFile('非普通文件或硬链接被保护')
        return cls(path, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_dev, s.st_ino, s.st_nlink,
                   getattr(s, 'st_birthtime_ns', None) if sys.platform == 'win32' else None)

    def verify(self, checked=None):
        if Snapshot._from_info(self.path, _validate_path(self.path, checked)) != self:
            raise UnsafeFile('文件在扫描后发生变化，请重新扫描')

    def verify_handle(self, observed):
        expected = (self.device, self.inode, self.size, self.mtime_ns, self.links)
        actual = (observed.st_dev, observed.st_ino, observed.st_size,
                  observed.st_mtime_ns, observed.st_nlink)
        if actual != expected:
            raise UnsafeFile('打开的文件身份、大小或修改时间已变化')
        if sys.platform == 'win32':
            # CPython Windows stat/lstat ctime can mean creation time while
            # fstat ctime means metadata-change time. Compare explicit birthtime
            # where available instead of equating those incompatible fields.
            if self.birthtime_ns is not None and getattr(observed, 'st_birthtime_ns', None) != self.birthtime_ns:
                raise UnsafeFile('打开的文件创建时间已变化')
        elif observed.st_ctime_ns != self.ctime_ns:
            raise UnsafeFile('打开的文件元数据时间已变化')


@contextmanager
def _open_checked(item: Snapshot, checked=None):
    item.verify(checked)
    flags = (os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
             | getattr(os, 'O_NONBLOCK', 0))
    with os.fdopen(os.open(item.path, flags), 'rb') as stream:
        item.verify_handle(os.fstat(stream.fileno()))
        yield stream
        item.verify_handle(os.fstat(stream.fileno()))
    item.verify(checked)


def digest(item: Snapshot, cancel=None, on_bytes=lambda _: None, *, checked=None):
    h = hashlib.sha256()
    with _open_checked(item, checked) as stream:
        remaining = item.size
        while remaining:
            checkpoint(cancel)
            block = stream.read(min(CHUNK, remaining))
            if not block:
                raise UnsafeFile('全文校验时文件长度发生变化')
            h.update(block)
            remaining -= len(block)
            on_bytes(len(block))
    return h.hexdigest()


SAMPLE_BLOCK = 16 * 1024


def sample(item: Snapshot, cancel=None, *, checked=None):
    """Reject different large files cheaply; a match never proves duplication."""
    h = hashlib.sha256()
    with _open_checked(item, checked) as stream:
        for offset in (0, (item.size - SAMPLE_BLOCK) // 2, item.size - SAMPLE_BLOCK):
            checkpoint(cancel)
            stream.seek(offset)
            block = stream.read(SAMPLE_BLOCK)
            if len(block) != SAMPLE_BLOCK:
                raise UnsafeFile('抽样时文件长度发生变化')
            h.update(block)
    return h.digest()


def equal(a: Snapshot, b: Snapshot, cancel=None):
    with _open_checked(a) as left, _open_checked(b) as right:
        while True:
            checkpoint(cancel)
            x, y = left.read(CHUNK), right.read(CHUNK)
            if x != y:
                return False
            if not x:
                return True


# Bound open handles and stream buffers even when there are thousands of copies.
COMPARE_BATCH = 8


def _equal_batch(keeper, items, cancel, checked):
    """Read the keeper once per block, checking every candidate's bytes."""
    with ExitStack() as stack:
        streams = [stack.enter_context(_open_checked(item, checked))
                   for item in (keeper, *items)]
        while True:
            checkpoint(cancel)
            block = streams[0].read(CHUNK)
            if any(stream.read(CHUNK) != block for stream in streams[1:]):
                return False
            if not block:
                return True


STREAM_THRESHOLD = 8 * 1024 * 1024


def _stream_batch(items, cancel, checked, measure, keep_singletons):
    """Split by exact bytes and compute SHA-256 in the same read.

    All members of a partition share a verified prefix. Hash that prefix
    once, then copy the hash state only when a block splits the partition.
    """
    size = items[0].size
    with ExitStack() as stack:
        streams = [(item, stack.enter_context(_open_checked(item, checked))) for item in items]
        partitions = [(streams, hashlib.sha256())]
        offset = 0
        while partitions and offset < size:
            checkpoint(cancel)
            width = min(CHUNK, size - offset)
            survivors = []
            read = 0
            for members, prefix in partitions:
                blocks = []
                for item, stream in members:
                    checkpoint(cancel)
                    data = stream.read(width)
                    if len(data) != width:
                        raise UnsafeFile('连续校验时文件长度发生变化')
                    blocks.append(data)
                    read += width
                if all(data == blocks[0] for data in blocks[1:]):
                    prefix.update(blocks[0])
                    survivors.append((members, prefix))
                    continue
                # Dictionaries resolve hash collisions with full bytes equality.
                branches = defaultdict(list)
                for data, member in zip(blocks, members):
                    branches[data].append(member)
                for data, branch in branches.items():
                    if keep_singletons or len(branch) > 1:
                        digest_state = prefix.copy()
                        digest_state.update(data)
                        survivors.append((branch, digest_state))
            offset += width
            partitions = survivors
            active = sum(len(members) for members, _ in partitions)
            measure(read, len(items) * size - active * (size - offset))
    return [(prefix.hexdigest(), [item for item, _ in members])
            for members, prefix in partitions]


def _stream_large_buckets(buckets, result, cancel, progress, checked):
    """Yield exact cohorts. Large buckets are split to bound open files."""
    total = sum(sum(item.size for item in items) for items in buckets)
    if not total:
        return
    completed = read_bytes = 0
    progress(Progress('内容校验', 'stream', 0, total, '字节', read_bytes=0))
    for items in buckets:
        split = len(items) > COMPARE_BATCH
        for start in range(0, len(items), COMPARE_BATCH):
            checkpoint(cancel)
            batch = items[start:start + COMPARE_BATCH]
            budget = sum(item.size for item in batch)
            high_water = 0

            def measure(read, covered):
                nonlocal read_bytes, high_water
                read_bytes += read
                high_water = max(high_water, covered)
                progress(Progress('内容校验', 'stream', completed + high_water,
                                  total, '字节', read_bytes=read_bytes))

            try:
                cohorts = _stream_batch(batch, cancel, checked, measure, split)
            except (OSError, UnsafeFile):
                # A faulty handle invalidates this batch's proof. Retry each
                # item independently; the exact cohort merge still follows.
                cohorts = []
                fallback_read = 0

                def measured_digest(count):
                    nonlocal fallback_read
                    fallback_read += count
                    measure(count, fallback_read)

                for item in batch:
                    checkpoint(cancel)
                    try:
                        cohorts.append((digest(item, cancel, measured_digest, checked=checked), [item]))
                    except (OSError, UnsafeFile) as error:
                        result.errors.append(f'{item.path}: {error}')
            measure(0, budget)
            completed += budget
            yield from cohorts


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


INLINE_FILE_LIMIT = 128 * 1024
INLINE_BUCKET_BUDGET = 16 * 1024 * 1024
INLINE_BUCKET_FILES = 8192


def _inline_groups(items, cancel, on_bytes, checked, errors):
    """Use exact byte keys for a small size bucket that fits the memory budget."""
    matches = defaultdict(list)
    for item in items:
        checkpoint(cancel)
        try:
            with _open_checked(item, checked) as stream:
                data = stream.read(item.size)
                on_bytes(len(data))
                if len(data) != item.size:
                    raise UnsafeFile('读取时文件长度发生变化')
        except (OSError, UnsafeFile) as error:
            errors.append(f'{item.path}: {error}')
        else:
            matches[data].append(item)
    return [(hashlib.sha256(data).hexdigest(), group)
            for data, group in matches.items() if len(group) > 1]


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
    checked = {}
    threshold = time.time_ns() - old_days * 86400 * 10**9

    def walk_error(error):
        result.errors.append(str(error))

    try:
        for root in roots:
            checkpoint(cancel)
            try:
                info = _validate_path(root, checked)
                if not stat.S_ISDIR(info.st_mode):
                    raise UnsafeFile('磁盘未挂载或目录不可用')
            except (OSError, UnsafeFile) as error:
                result.errors.append(f'{root}：{error}')
                continue
            for directory, dirs, names in os.walk(root, followlinks=False, onerror=walk_error):
                checkpoint(cancel)
                # Metadata enumeration checks ancestors once per directory. No
                # cached authorization is used when opening contents or cleaning.
                try:
                    parent = Path(directory)
                    _validate_path(parent, checked)
                except (OSError, UnsafeFile) as error:
                    dirs[:] = []
                    result.skipped += 1
                    result.errors.append(f'{directory}：{error}')
                    continue
                progress(Progress(f'正在遍历 · 已发现 {result.scanned:,} 个文件 · 总量统计中 · {directory}', 'discover', result.scanned))
                kept = []
                for name in sorted(dirs):
                    child = parent / name
                    if child in boundaries or (mode == 'computer' and os.path.ismount(child)):
                        if child not in roots:
                            result.excluded.append(f'{child}：独立挂载点，未从当前磁盘进入')
                        continue
                    try:
                        _check_directory(child, child.lstat(), checked)
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
                        path = parent / name
                        item = Snapshot._from_info(path, path.lstat())
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
                            progress(Progress(f'正在遍历 · 已发现 {result.scanned:,} 个文件 · 总量统计中', 'discover', result.scanned))
                    except UnsafeFile:
                        result.skipped += 1
                    except OSError as error:
                        result.errors.append(str(error))
        checked.clear()
        buckets = [items for items in sizes.values() if len(items) > 1]
        sizes.clear()
        sample_total = sum(len(items) for items in buckets if items[0].size > 128 * 1024)
        sampled = 0
        candidates = []
        large_buckets = []
        inline_buckets = []
        progress(Progress('抽样排除不同文件', 'sample', 0, sample_total))
        for items in buckets:
            size = items[0].size
            if size <= INLINE_FILE_LIMIT:
                if len(items) <= INLINE_BUCKET_FILES and size * len(items) <= INLINE_BUCKET_BUDGET:
                    inline_buckets.append(items)
                else:
                    candidates.extend(items)
                continue
            samples = defaultdict(list)
            for item in items:
                checkpoint(cancel)
                try:
                    samples[sample(item, cancel, checked=checked)].append(item)
                except (OSError, UnsafeFile) as error:
                    result.errors.append(f'{item.path}: {error}')
                sampled += 1
                progress(Progress(f'抽样排除不同文件 · {sampled:,}/{sample_total:,} 个（含跳过）', 'sample', sampled, sample_total))
            for matches in samples.values():
                if len(matches) > 1:
                    if matches[0].size >= STREAM_THRESHOLD:
                        large_buckets.append(matches)
                    else:
                        candidates.extend(matches)
        # Separate size keys are essential: samples are only rejection filters.
        buckets.clear()
        checked.clear()
        hashes = defaultdict(list)
        total_bytes = (sum(item.size for item in candidates)
                       + sum(len(items) * items[0].size for items in inline_buckets))
        read_bytes = 0
        def hashed_bytes(count):
            nonlocal read_bytes
            read_bytes += count
            progress(Progress('全文校验', 'hash', read_bytes, total_bytes, '字节', read_bytes=read_bytes))
        progress(Progress('全文校验', 'hash', 0, total_bytes, '字节', read_bytes=0))
        exact = defaultdict(list)
        for items in inline_buckets:
            for sha, group in _inline_groups(items, cancel, hashed_bytes, checked, result.errors):
                exact[(group[0].size, sha)].append(group)
        inline_buckets.clear()
        for item in candidates:
            checkpoint(cancel)
            try:
                sha = digest(item, cancel, on_bytes=hashed_bytes, checked=checked)
                hashes[(item.size, sha)].append(item)
            except (OSError, UnsafeFile) as error:
                result.errors.append(f'{item.path}: {error}')
        candidates.clear()
        checked.clear()
        for sha, cohort in _stream_large_buckets(large_buckets, result, cancel, progress, checked):
            exact[(cohort[0].size, sha)].append(cohort)
        large_buckets.clear()
        matches_list = [(sha, matches) for (_, sha), matches in hashes.items() if len(matches) > 1]
        hashes.clear()
        compare_total = (sum(len(matches) - 1 for _, matches in matches_list)
                         + sum(len(cohorts) - 1 for cohorts in exact.values()))
        compared = 0
        checked.clear()
        def compared_files(count):
            nonlocal compared
            compared += count
            progress(Progress(f'逐字节复核 · {compared:,}/{compare_total:,} 个（含跳过）',
                              'compare', compared, compare_total))

        progress(Progress('逐字节复核', 'compare', 0, compare_total))
        for sha, matches in matches_list:
            partitions = [[matches[0]]]
            for start in range(1, len(matches), COMPARE_BATCH):
                checkpoint(cancel)
                batch = matches[start:start + COMPARE_BATCH]
                batch_count = len(batch)
                try:
                    matched_keeper = bool(partitions and _equal_batch(partitions[0][0], batch, cancel, checked))
                except (OSError, UnsafeFile):
                    matched_keeper = False
                if matched_keeper:
                    partitions[0].extend(batch)
                    compared_files(len(batch))
                    continue
                # Revalidate representatives so a vanished keeper cannot
                # hide otherwise valid matches in the rest of this bucket.
                valid_partitions = []
                for group in partitions:
                    try:
                        group[0].verify()
                        valid_partitions.append(group)
                    except (OSError, UnsafeFile) as error:
                        result.errors.append(f'{group[0].path}: {error}')
                        batch = group[1:] + batch
                partitions = valid_partitions
                for item in batch:
                    try:
                        for group in partitions:
                            if equal(group[0], item, cancel):
                                group.append(item)
                                break
                        else:
                            partitions.append([item])
                    except (OSError, UnsafeFile) as error:
                        result.errors.append(f'{item.path}: {error}')
                compared_files(batch_count)
            _confirm_groups(sha, partitions, result, cancel, checked)
        for (_, sha), cohorts in exact.items():
            checkpoint(cancel)
            partitions = [cohorts[0]]
            for cohort in cohorts[1:]:
                checkpoint(cancel)
                try:
                    for group in partitions:
                        if equal(group[0], cohort[0], cancel):
                            group.extend(cohort)
                            break
                    else:
                        partitions.append(cohort)
                except (OSError, UnsafeFile) as error:
                    result.errors.append(f'{cohort[0].path}: {error}')
                    # Internally verified cohorts remain useful even when a
                    # representative cannot be compared with another cohort.
                    partitions.append(cohort)
                compared_files(1)
            _confirm_groups(sha, partitions, result, cancel, checked)
        result.groups.sort(key=lambda g: g.files[0].size * (len(g.files)-1), reverse=True)
        result.review.sort(key=lambda f: f.size, reverse=True)
        checkpoint(cancel)
        progress(Progress('扫描完成', 'done', 1, 1))
    except Cancelled:
        result.cancelled = True
        result.groups.clear()  # incomplete scans never authorize cleanup
    return result


def _confirm_groups(sha, partitions, result, cancel, checked):
    for group in partitions:
        if len(group) < 2:
            continue
        confirmed = []
        for item in group:
            checkpoint(cancel)
            try:
                item.verify(checked)
                confirmed.append(item)
            except (UnsafeFile, OSError) as error:
                result.errors.append(f'{item.path}: {error}')
        if len(confirmed) > 1:
            result.groups.append(DuplicateGroup(uuid.uuid4().hex, sha, tuple(confirmed)))


@dataclass(frozen=True)
class Plan:
    id: str
    groups: tuple[DuplicateGroup, ...]
    targets: tuple[Snapshot, ...]
    created: float
    disposable_root: Path | None = None
    root_identity: tuple[int, int] | None = None
    blocked: tuple[tuple[Path, str], ...] = ()

    @property
    def bytes(self):
        return sum(f.size for f in self.targets)


@lru_cache(maxsize=1)
def _mac_listxattr():
    import ctypes
    libc = ctypes.CDLL('/usr/lib/libSystem.B.dylib', use_errno=True)
    call = libc.listxattr
    call.argtypes = (ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int)
    call.restype = ctypes.c_ssize_t
    return call


@lru_cache(maxsize=1)
def _windows_stream_api():
    import ctypes
    from ctypes import wintypes

    class StreamData(ctypes.Structure):
        _fields_ = [('size', ctypes.c_int64), ('name', wintypes.WCHAR * 296)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    first, more, close = kernel.FindFirstStreamW, kernel.FindNextStreamW, kernel.FindClose
    first.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(StreamData), wintypes.DWORD)
    first.restype = wintypes.HANDLE
    more.argtypes = (wintypes.HANDLE, ctypes.POINTER(StreamData))
    more.restype = wintypes.BOOL
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    return StreamData, first, more, close


def _has_side_data(path):
    """Protect opaque attributes and named streams without reading their values."""
    if sys.platform == 'darwin':
        import ctypes
        if os.path.lexists(path.with_name('._' + path.name)):
            return True
        attr_list = _mac_listxattr()
        name = os.fsencode(path)
        size = attr_list(name, None, 0, 1)  # XATTR_NOFOLLOW
        if size == 0:
            return False
        if 0 < size <= 65536:
            names = ctypes.create_string_buffer(size)
            read = attr_list(name, names, size, 1)
            if read >= 0:
                # macOS can stamp newly created files with Apple provenance.
                # User attributes, Finder metadata and forks remain protected.
                return bool(set(names.raw[:read].split(b'\0')) - {b'', b'com.apple.provenance'})
        if size > 65536:
            return True
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), path)
    if sys.platform == 'win32':
        import ctypes
        Data, first, more, close = _windows_stream_api()
        data = Data()
        handle = first(str(path), 0, ctypes.byref(data), 0)
        if handle == ctypes.c_void_p(-1).value:
            code = ctypes.get_last_error()
            if code in (38, 87):  # No streams or filesystem has no stream support.
                return False
            raise ctypes.WinError(code)
        try:
            if data.name != '::$DATA':
                return True
            if more(handle, ctypes.byref(data)):
                return True
            code = ctypes.get_last_error()
            if code != 38:
                raise ctypes.WinError(code)
            return False
        finally:
            close(handle)
    return bool(os.listxattr(path, follow_symlinks=False)) if hasattr(os, 'listxattr') else False


def _protect_side_data(item):
    item.verify()
    if _has_side_data(item.path):
        raise UnsafeFile(f'含附加文件数据，保留：{item.path.name}')


def disposable_scope(path):
    if path is None:
        raise UnsafeFile('请先指定只存放可弃用副本的文件夹')
    root = assert_safe(Path(path))
    home = Path.home()
    if (not root.is_dir() or root == home or root in home.parents
            or os.path.ismount(root)):
        raise UnsafeFile('请选择单独存放可弃用副本的文件夹')
    return root


def _root_identity(root):
    info = root.lstat()
    return info.st_dev, info.st_ino


def cleanup_candidates(result, root=None):
    if result is None or result.cancelled:
        return {}
    if root is None:
        return {item.path: item for group in result.groups for item in group.files}
    return {item.path: item for group in result.groups
            if any(not f.path.is_relative_to(root) for f in group.files)
            for item in group.files if item.path.is_relative_to(root)}


def make_plan(result: ScanResult, selected: list[Path], disposable_root=None, *, skip_blocked=False):
    if result is None or result.cancelled:
        raise UnsafeFile('扫描未完成')
    wanted = {Path(p) for p in selected}
    if not wanted:
        raise UnsafeFile('请先勾选文件')
    root = disposable_scope(disposable_root) if disposable_root is not None else None
    known = cleanup_candidates(result, root)
    if wanted - known.keys():
        raise UnsafeFile('只可清理本次扫描核验的重复文件')
    groups = [g for g in result.groups if any(f.path in wanted for f in g.files)]
    if any(all(f.path in wanted for f in g.files) for g in groups):
        raise UnsafeFile('每组至少保留一份文件')
    blocked = {}
    for path in sorted(wanted):
        try:
            _protect_side_data(known[path])
        except (OSError, UnsafeFile) as error:
            if not skip_blocked:
                raise
            blocked[path] = str(error)
    if skip_blocked:
        blocked.update(_open_file_problems(wanted - blocked.keys()))
    kept_groups = []
    for group in groups:
        targets = [f for f in group.files if f.path in wanted and f.path not in blocked]
        if not targets:
            continue
        # A blocked target never silently becomes the protected keeper.
        keepers = [f for f in group.files if f.path not in wanted
                   and (root is None or not f.path.is_relative_to(root))]
        existing = []
        for keeper in keepers:
            try:
                keeper.verify()
            except (OSError, UnsafeFile):
                if not skip_blocked:
                    # Execution rechecks the original scan snapshot as before.
                    existing.append(keeper)
            else:
                existing.append(keeper)
        if not existing:
            for item in targets:
                blocked[item.path] = '保留副本已变化或无法检查，请重新扫描'
            continue
        kept_groups.append(DuplicateGroup(group.id, group.sha256, tuple(existing + targets)))
    return Plan(uuid.uuid4().hex, tuple(kept_groups), tuple(known[p] for p in sorted(wanted - blocked.keys())),
                time.monotonic(), root, _root_identity(root) if root is not None else None,
                tuple(sorted(blocked.items())))


@lru_cache(maxsize=1)
def _windows_exclusive_api():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create_file = kernel.CreateFileW
    create_file.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                            wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create_file.restype = wintypes.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = (wintypes.HANDLE,)
    close_handle.restype = wintypes.BOOL
    return create_file, close_handle, ctypes.c_void_p(-1).value


def _windows_check_exclusive(paths):
    import ctypes
    create_file, close_handle, invalid_handle = _windows_exclusive_api()
    for path in paths:
        # GENERIC_READ, no sharing, OPEN_EXISTING. Never writes to the file.
        handle = create_file(str(path), 0x80000000, 0, None, 3, 0x00200000, None)
        if handle == invalid_handle:
            error = ctypes.get_last_error()
            if error in (32, 33):  # Sharing or lock violation
                raise UnsafeFile(f'文件正在使用：{path.name}')
            raise ctypes.WinError(error)
        if not close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _open_file_problems(paths):
    # OS process visibility is permission dependent. An observed handle
    # vetoes cleanup; absence is not evidence that a path is dispensable.
    problems = {}
    if sys.platform == 'win32':
        for path in paths:
            try:
                _windows_check_exclusive((path,))
            except (OSError, UnsafeFile) as error:
                problems[path] = str(error)
        return problems
    import psutil
    targets = {os.path.normcase(os.path.realpath(p)): p for p in paths}
    for process in psutil.process_iter():
        try:
            for file in process.open_files():
                path = targets.get(os.path.normcase(os.path.realpath(file.path)))
                if path is not None:
                    problems[path] = f'文件正在使用：{path.name}'
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return problems


def _reject_open_files(paths):
    problems = _open_file_problems(paths)
    if problems:
        raise UnsafeFile(next(iter(problems.values())))


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
    root = disposable_scope(plan.disposable_root) if plan.disposable_root is not None else None
    targets = {f.path for f in plan.targets}
    if not targets:
        raise UnsafeFile('没有可清理的文件')
    if root is not None and (_root_identity(root) != plan.root_identity or any(
            not path.is_relative_to(root) for path in targets)):
        raise UnsafeFile('清理范围已变化，请重新扫描')
    for item in plan.targets:
        _protect_side_data(item)
    _reject_open_files(targets)
    keepers = {}
    # Preflight ALL items before the first mutation.
    for group in plan.groups:
        keeper = next((f for f in group.files if f.path not in targets
                       and (root is None or not f.path.is_relative_to(root))), None)
        if keeper is None:
            raise UnsafeFile('该组缺少保留副本')
        if root is not None and any(_root_identity(parent) == plan.root_identity for parent in keeper.path.parents):
            raise UnsafeFile('保留副本位于清理范围内')
        keeper.verify()
        if digest(keeper) != group.sha256:
            raise UnsafeFile('保留文件内容已改变')
        for f in group.files:
            if f.path in targets:
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
                if root is not None and _root_identity(disposable_scope(root)) != plan.root_identity:
                    raise UnsafeFile('清理范围已变化，请重新扫描')
                if not equal(item, keeper):
                    raise UnsafeFile('执行时内容不一致')
                _reject_open_files((item.path,))
                _protect_side_data(item)
                record({**entry, 'status': 'intent'})
                item.verify()
                keeper.verify()
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
