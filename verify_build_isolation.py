"""Regression: rebuilding while a frozen process is alive must not rewrite its PYZ."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent


def checksum(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    latest = ROOT / 'work/latest-build.json'
    first = json.loads(latest.read_text(encoding='utf-8'))
    first_dir = Path(first['directory'])
    binary = first_dir / 'dist' / ('ClearGuard.app/Contents/MacOS/ClearGuard' if sys.platform == 'darwin' else 'ClearGuard/ClearGuard.exe')
    before = checksum(binary)
    qa = ROOT / 'work' / 'build-isolation-qa'
    qa.mkdir(exist_ok=True)
    target = Path(tempfile.mkdtemp(dir=qa, prefix='run-'))
    ready, proceed, report = target / 'ready', target / 'proceed', target / 'selftest.json'
    with (target / 'child.log').open('w') as log:
        child = subprocess.Popen([str(binary), '--self-test', '--qa-root', str(target),
                                  '--qa-ready', str(ready), '--qa-proceed', str(proceed),
                                  '--report', str(report)], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                if child.poll() is not None:
                    raise RuntimeError('Waiting test process exited: ' + str(report))
                if time.monotonic() > deadline:
                    raise TimeoutError('Bundled QA process did not become ready')
                time.sleep(0.05)
            with (target / 'rebuild.log').open('w') as build_log:
                subprocess.run([sys.executable, str(ROOT / 'build.py')], cwd=ROOT,
                               stdout=build_log, stderr=subprocess.STDOUT, check=True, timeout=180)
            second = json.loads(latest.read_text(encoding='utf-8'))
            assert second['directory'] != first['directory'], 'Build reused the active directory'
            assert checksum(binary) == before, 'Active executable was overwritten'
            proceed.write_text('Continue loading dependencies and scanning fixtures', encoding='utf-8')
            assert child.wait(timeout=60) == 0, str(report)
            outcome = json.loads(report.read_text(encoding='utf-8'))
            assert outcome['status'] == 'passed', outcome
            (target / 'result.json').write_text(json.dumps({
                'status': 'passed', 'active_executable_unchanged': True,
                'distinct_build_directories': True, 'old_process_completed_real_fixture_flow': True,
                'first_build': first['build_id'], 'second_build': second['build_id'],
            }, indent=2), encoding='utf-8')
            print('Live-process rebuild regression passed:', target / 'result.json')
        finally:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=10)


if __name__ == '__main__':
    main()
