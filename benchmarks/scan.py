"""Reproduce the duplicate and near-match scenario with generated files."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clearguard import core

MIB = 1024 * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size-mib', type=int, default=16, help='Size of each of the eight files (8–4096 MiB)')
    args = parser.parse_args()
    if not 8 <= args.size_mib <= 4096:
        parser.error('--size-mib must be between 8 and 4096')

    qa = ROOT / 'work' / 'benchmarks'
    qa.mkdir(parents=True, exist_ok=True)
    block = hashlib.shake_256(b'ClearGuard repeatable scan fixture').digest(MIB)
    with tempfile.TemporaryDirectory(dir=qa) as tmp:
        folder = Path(tmp)
        for index in range(8):
            with (folder / f'file-{index}.bin').open('wb') as file:
                first = bytearray(block)
                if index >= 4:
                    first[MIB // 4] ^= index
                file.write(first)
                for _ in range(args.size_mib - 1):
                    file.write(block)

        read_by_phase = {}

        def progress(event):
            if isinstance(event, core.Progress) and event.read_bytes is not None:
                read_by_phase[event.phase] = event.read_bytes

        # The application's source repository is protected in production.
        # Relax only that policy here and only for the generated fixture.
        with (patch.object(core, 'PROJECT_MARKERS', {'PROTECTED_BENCHMARK_PROJECT'}),
              patch.object(core, 'MANAGED_WORKSPACES', ())):
            start = time.perf_counter()
            result = core.scan(folder, progress=progress)
            elapsed = time.perf_counter() - start
        expected = {f'file-{i}.bin' for i in range(4)}
        assert result.scanned == 8 and len(result.groups) == 1 and not result.errors
        assert {item.path.name for item in result.groups[0].files} == expected
        print(json.dumps({
            'platform': platform.system(),
            'architecture': platform.machine(),
            'files': result.scanned,
            'fixture_bytes': result.bytes_seen,
            'duplicate_groups': len(result.groups),
            'content_sha256': result.groups[0].sha256,
            'bytes_read_in_measured_phases': read_by_phase,
            'seconds': round(elapsed, 4),
        }, indent=2))


if __name__ == '__main__':
    main()
