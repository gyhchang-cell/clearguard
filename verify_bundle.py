"""Open the actual bundled Tk executable and exit without any scan or cleanup."""
from pathlib import Path
import platform
import subprocess
root = Path(__file__).resolve().parent
binary = root / 'dist' / ('ClearGuard.app/Contents/MacOS/ClearGuard' if platform.system() == 'Darwin' else 'ClearGuard/ClearGuard.exe')
subprocess.run([str(binary), '--smoke-test'], check=True, timeout=30)
print('Packaged desktop UI smoke passed; no file scan or cleanup.')
