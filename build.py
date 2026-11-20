"""Build on the target operating system; all caches remain in this project."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parent
os.environ['PYINSTALLER_CONFIG_DIR'] = str(root / '.cache' / 'pyinstaller')
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                '--windowed', '--name', 'ClearGuard', '--onedir',
                '--distpath', str(root / 'dist'), '--workpath', str(root / 'build'),
                '--specpath', str(root / 'work'), str(root / 'run.py')], cwd=root, check=True)
