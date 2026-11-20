"""Package the latest immutable local build for QA, never for automatic publishing."""
import json
from pathlib import Path
import platform
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent


def main():
    manifest_path = ROOT / 'work' / 'latest-build.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    target = Path(manifest['directory'])
    if target.parent != ROOT / 'local-builds':
        raise ValueError('Build must stay inside this project')
    if platform.system() == 'Darwin':
        archive = target / f'ClearGuard-development-macos-{platform.machine()}.zip'
        subprocess.run(['ditto', '-c', '-k', '--sequesterRsrc', '--keepParent',
                        str(target / 'dist' / 'ClearGuard.app'), str(archive)], check=True)
    else:
        base = target / 'ClearGuard-development-windows-x64'
        archive = Path(shutil.make_archive(str(base), 'zip', target / 'dist', 'ClearGuard'))
    manifest['archive'] = str(archive)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print('Local QA package:', archive)


if __name__ == '__main__':
    main()
