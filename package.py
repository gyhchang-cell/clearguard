"""Preserve macOS bundle permissions/symlinks inside a distributable ZIP."""
from pathlib import Path
import platform
import shutil
import subprocess

root = Path(__file__).resolve().parent
if platform.system() == 'Darwin':
    name = f'ClearGuard-macos-{platform.machine()}.zip'
    subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent',
                    str(root / 'dist' / 'ClearGuard.app'), str(root / 'dist' / name)], check=True)
else:
    name = 'ClearGuard-windows-x64'
    shutil.make_archive(str(root / 'dist' / name), 'zip', root / 'dist', 'ClearGuard')
