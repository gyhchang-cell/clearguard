"""Package and exercise the real macOS image or Windows installer."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import sys
import sysconfig
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from clearguard import __version__

NSIS_SHA256 = 'c7d27f780ddb6cffb4730138cd1591e841f4b7edb155856901cdf5f214394fa1'
NSIS_URL = 'https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.11/nsis-3.11.zip'


def run(*args, **kwargs):
    subprocess.run([str(arg) for arg in args], check=True, timeout=120, **kwargs)


def file_sha(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def write_licenses(folder):
    folder.mkdir()
    shutil.copy2(ROOT / 'LICENSE', folder / 'ClearGuard.txt')
    for package in ('pyinstaller', 'psutil', 'Send2Trash'):
        dist = importlib.metadata.distribution(package)
        files = [dist.locate_file(file) for file in dist.files
                 if '.dist-info' in str(file) and file.name.lower().startswith(('license', 'copying'))]
        if not files:
            raise RuntimeError(f'Missing license files for {package}')
        for index, path in enumerate(files):
            shutil.copy2(path, folder / f'{package}-{index+1}.txt')
    py_candidates = [Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt',
                     Path(sys.base_prefix) / 'LICENSE.txt']
    for path in py_candidates:
        if path.is_file():
            shutil.copy2(path, folder / 'Python.txt')
            break
    else:
        raise RuntimeError('Missing Python license')
    (folder / 'Runtime-sources.txt').write_text(
        'Tk / Tcl: https://www.tcl-lang.org/software/tcltk/license.html\n'
        'Python runtime: https://www.python.org/downloads/\n'
        'Windows installer: https://nsis.sourceforge.io/License\n', encoding='utf-8')


def selftest(binary, qa):
    report = qa / 'selftest.json'
    run(binary, '--self-test', '--qa-root', qa, '--report', report)
    payload = json.loads(report.read_text(encoding='utf-8'))
    if payload['status'] != 'passed' or payload['native_trash_called']:
        raise RuntimeError('Installed application failed its fixture checks')


def mac_package(target, output, qa):
    staging = target / 'image-source'
    staging.mkdir()
    app = 'ClearGuard.app'
    run('ditto', target / 'dist' / app, staging / app)
    (staging / 'Applications').symlink_to('/Applications', target_is_directory=True)
    write_licenses(staging / 'Licenses')
    dmg = output / f'ClearGuard-{__version__}-macos-{platform.machine()}.dmg'
    run('hdiutil', 'create', '-quiet', '-volname', f'ClearGuard {__version__}',
        '-fs', 'HFS+', '-format', 'UDZO', '-srcfolder', staging, dmg)
    run('hdiutil', 'verify', '-quiet', dmg)
    mount = qa / 'mounted'
    mount.mkdir()
    run('hdiutil', 'attach', '-readonly', '-nobrowse', '-noautoopen', '-mountpoint', mount, dmg)
    copy = qa / app
    try:
        if not (mount / 'Applications').is_symlink():
            raise RuntimeError('Missing drag-to-Applications link')
        run('ditto', mount / app, copy)
    finally:
        run('hdiutil', 'detach', mount)
    run('codesign', '--verify', '--deep', '--strict', copy)
    selftest(copy / 'Contents' / 'MacOS' / 'ClearGuard', qa)


def nsis(target):
    # The mirror hosts the unmodified upstream NSIS archive. Verify it against
    # the official nsis-dev release-data SHA-256 before extracting or running.
    assets = target / 'nsis-tools'
    assets.mkdir()
    archive = assets / 'nsis.zip'
    run('curl.exe', '-fSL', '--retry', '2', NSIS_URL, '-o', archive)
    if file_sha(archive) != NSIS_SHA256:
        raise RuntimeError('NSIS archive SHA-256 mismatch')
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(assets)
    return assets / 'nsis-3.11'


def windows_package(target, output, qa):
    app = target / 'dist' / 'ClearGuard'
    write_licenses(app / 'Licenses')
    tool = nsis(target)
    shutil.copy2(tool / 'COPYING', app / 'Licenses' / 'NSIS.txt')
    shutil.copy2(ROOT / 'installers' / 'windows.nsi', target)
    # Remove only paths owned by this installer, even when the install
    # directory also holds files the user added after installation.
    files = sorted(path.relative_to(app) for path in app.rglob('*') if path.is_file())
    dirs = sorted((p.relative_to(app) for p in app.rglob('*') if p.is_dir()),
                  key=lambda path: len(path.parts), reverse=True)
    removals = [f'Delete "$INSTDIR\\{name}"' for name in files]
    removals.extend(f'RMDir "$INSTDIR\\{name}"' for name in dirs)
    (target / 'uninstall-files.nsh').write_text('\n'.join(removals) + '\n', encoding='utf-8')
    run(tool / 'makensis.exe', f'/DVERSION={__version__}', 'windows.nsi', cwd=target)
    installer = output / f'ClearGuard-{__version__}-windows-x64-setup.exe'
    installed = qa / 'installed'
    run(installer, '/S', f'/D={installed}')
    selftest(installed / 'ClearGuard.exe', qa)
    extra = installed / 'unowned-qa-file.txt'
    extra.write_text('Must survive uninstall', encoding='utf-8')
    # _?= runs in-place so subprocess waits for actual uninstall completion.
    run(installed / 'Uninstall.exe', '/S', f'_?={installed}')
    if (installed / 'ClearGuard.exe').exists() or not extra.exists():
        raise RuntimeError('Uninstall did not preserve unrelated files')
    shutil.make_archive(str(output / f'ClearGuard-{__version__}-windows-x64-portable'),
                        'zip', app.parent, app.name)


def main():
    manifest = json.loads((ROOT / 'work' / 'latest-build.json').read_text(encoding='utf-8'))
    target = Path(manifest['directory'])
    if target.parent != ROOT / 'local-builds' or manifest['development_only'] or manifest['version'] != __version__:
        raise ValueError('Run build.py --release first')
    output, qa = target / 'release', target / 'installer-qa'
    output.mkdir()
    qa.mkdir()
    if sys.platform == 'darwin':
        mac_package(target, output, qa)
    elif sys.platform == 'win32' and platform.machine().casefold() in {'amd64', 'x86_64'}:
        windows_package(target, output, qa)
    else:
        raise ValueError('Distribution builds require macOS or Windows x64')
    hashes = {path.name: file_sha(path) for path in sorted(output.iterdir())}
    (output / 'SHA256-platform.txt').write_text(
        ''.join(f'{sha}  {name}\n' for name, sha in hashes.items()), encoding='utf-8')
    print(json.dumps({'assets': hashes, 'directory': str(output)}, indent=2))
    if 'GITHUB_OUTPUT' in os.environ:
        with open(os.environ['GITHUB_OUTPUT'], 'a', encoding='utf-8') as github_output:
            github_output.write(f'path={output.as_posix()}\n')


if __name__ == '__main__':
    main()
