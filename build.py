"""Create an immutable local development build; never overwrite a running app."""
import argparse
import datetime
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import uuid

from clearguard import __version__

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', action='store_true', help='Build a versioned distribution app')
    args = parser.parse_args()
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
        bundle_id = 'io.github.gyhchang-cell.clearguard'
        if not args.release:
            bundle_id += '.dev.b' + build_id.replace('-', '')
        command += ['--osx-bundle-identifier', bundle_id]
    command += [str(ROOT / 'run.py')]
    subprocess.run(command, cwd=ROOT, env=env, check=True)
    if sys.platform == 'darwin':
        app = target / 'dist' / 'ClearGuard.app'
        plist = app / 'Contents' / 'Info.plist'
        with plist.open('rb') as stream:
            info = plistlib.load(stream)
        info.update(CFBundleShortVersionString=__version__, CFBundleVersion=__version__)
        with plist.open('wb') as stream:
            plistlib.dump(info, stream)
        subprocess.run(['codesign', '--force', '--sign', '-', str(app)], check=True)
    elif not (target / 'dist' / 'ClearGuard' / 'ClearGuard.exe').is_file():
        raise ValueError('Windows app was not produced')
    manifest = {'build_id': build_id, 'directory': str(target), 'status': 'unverified',
                'development_only': not args.release, 'version': __version__}
    work = ROOT / 'work'
    work.mkdir(exist_ok=True)
    temporary = work / 'latest-build.tmp'
    temporary.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    temporary.replace(work / 'latest-build.json')
    print('Immutable build:', target)


if __name__ == '__main__':
    main()
