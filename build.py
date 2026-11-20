"""Create an immutable local development build; never overwrite a running app."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parent


def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    build_id = stamp + '-' + uuid.uuid4().hex[:8]
    target = ROOT / 'local-builds' / build_id
    target.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYINSTALLER_CONFIG_DIR=str(ROOT / '.cache' / 'pyinstaller'))
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
               '--windowed', '--name', 'ClearGuard', '--onedir',
               '--distpath', str(target / 'dist'), '--workpath', str(target / 'build'),
               '--specpath', str(target / 'spec')]
    if sys.platform == 'darwin':
        command += ['--osx-bundle-identifier', 'io.github.gyhchang-cell.clearguard.dev.b' + build_id.replace('-', '')]
    command += [str(ROOT / 'run.py')]
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    manifest = {'build_id': build_id, 'directory': str(target), 'status': 'unverified',
                'development_only': True}
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    temporary = work / 'latest-build.tmp'
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temporary.replace(work / 'latest-build.json')
    print('Local development build:', target)


if __name__ == '__main__':
    main()
