"""Version checks, reproducible release inputs and explicit Windows packaging."""
from __future__ import annotations
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from course_auto_assistant import __version__


def version_check():
    version = (ROOT / 'VERSION').read_text().strip()
    if not re.fullmatch(r'v\d+\.\d+\.\d+', version) or version != 'v' + __version__:
        raise ValueError('VERSION 与应用版本不一致')
    text = (ROOT / 'CHANGELOG.md').read_text(encoding='utf-8')
    if not text.startswith(f'# 更新记录\n\n## {version} '):
        raise ValueError('CHANGELOG 顶部没有当前版本')
    section = re.split(r'\n## ', text.split('\n', 2)[2], maxsplit=1)[0]
    output = ROOT / 'build'
    output.mkdir(exist_ok=True)
    (output / 'release-notes.md').write_text(section + '\n', encoding='utf-8')
    return version


def notices(dest):
    dest.mkdir(parents=True, exist_ok=True)
    # Preserve installed third-party license files rather than assigning a new
    # project license on behalf of the repository owner.
    for name in ('playwright', 'greenlet', 'pyee', 'typing_extensions'):
        dist = importlib.metadata.distribution(name)
        for path in dist.files or []:
            if 'license' in str(path).lower() or 'copying' in str(path).lower():
                source = Path(dist.locate_file(path))
                if source.is_file():
                    target = dest / name / str(path).replace('..', '_')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, dest / 'Python-LICENSE.txt')


def build():
    if os.name != 'nt':
        raise RuntimeError('Windows EXE 必须在 Windows 上打包')
    version = version_check()
    legal = ROOT / 'build' / 'third-party-notices'
    notices(legal)
    name = f'Course-Auto-Assistant-{version}-windows-x64'
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--onefile', '--windowed', '--noupx', '--name', name,
                    '--collect-all', 'playwright', '--add-data', f'{legal};third-party-notices',
                    str(ROOT / 'main.py')], cwd=ROOT, check=True)
    exe = ROOT / 'dist' / (name + '.exe')
    report = ROOT / 'build' / 'smoke-report.json'
    result = subprocess.run([str(exe), '--self-test', str(report)], cwd=ROOT, timeout=120)
    if result.returncode or not report.exists() or not json.loads(report.read_text(encoding='utf-8')).get('ok'):
        raise RuntimeError('打包后的EXE自检未通过，禁止发布')
    package = ROOT / 'dist' / (name + '.zip')
    with zipfile.ZipFile(package, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.write(exe, exe.name)
        for source in (ROOT / 'README.md', ROOT / 'CHANGELOG.md', ROOT / 'docs' / 'USER_GUIDE.md', ROOT / 'docs' / 'PRIVACY.md'):
            archive.write(source, source.name)
        for source in legal.rglob('*'):
            if source.is_file():
                archive.write(source, 'third-party-notices/' + str(source.relative_to(legal)))
    items = [exe, package]
    hashes = [f'{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}' for p in items]
    (ROOT / 'dist' / 'SHA256SUMS.txt').write_text('\n'.join(hashes) + '\n', encoding='utf-8')
    print('Build and EXE smoke test passed:', version)


if __name__ == '__main__':
    if sys.argv[1:] == ['check']:
        print(version_check())
    elif sys.argv[1:] == ['build']:
        build()
    else:
        raise SystemExit('Usage: py scripts/release_tools.py check|build')
