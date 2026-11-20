"""Verify a fresh extracted local QA archive, including the actual scan UI flow."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent


def main():
    path = ROOT / 'work' / 'latest-build.json'
    manifest = json.loads(path.read_text(encoding='utf-8'))
    archive = Path(manifest['archive'])
    if archive.parent.parent != ROOT / 'local-builds':
        raise ValueError('QA archive must be inside this project')
    with zipfile.ZipFile(archive) as compressed:
        if compressed.testzip() is not None:
            raise ValueError('Archive CRC validation failed')
    qa = ROOT / 'work' / 'bundle-qa'
    qa.mkdir(exist_ok=True)
    extracted = Path(tempfile.mkdtemp(dir=qa, prefix='extracted-'))
    if platform.system() == 'Darwin':
        subprocess.run(['ditto', '-x', '-k', str(archive), str(extracted)], check=True)
        binary = extracted / 'ClearGuard.app/Contents/MacOS/ClearGuard'
    else:
        with zipfile.ZipFile(archive) as compressed:
            compressed.extractall(extracted)
        binary = extracted / 'ClearGuard/ClearGuard.exe'
    report = extracted / 'selftest.json'
    subprocess.run([str(binary), '--self-test', '--qa-root', str(qa), '--report', str(report)],
                   check=True, timeout=90, env={**os.environ, 'PYTHONFAULTHANDLER': '1'})
    verified = json.loads(report.read_text(encoding='utf-8'))
    if verified['status'] != 'passed':
        raise ValueError('Bundled self-test failed')
    with archive.open('rb') as stream:
        archive_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
    manifest.update(status='verified_locally_not_user_approved',
                    archive_sha256=archive_hash,
                    verified_extracted_app=str(binary.parents[2] if platform.system() == 'Darwin' else binary.parent),
                    qa_report=str(report))
    path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Extracted local QA archive passed:', ', '.join(verified['checks']))


if __name__ == '__main__':
    main()
