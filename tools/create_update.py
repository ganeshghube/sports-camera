#!/usr/bin/env python3
"""Create a versioned application ZIP and updates/latest.json for GitHub publishing.

Typical use:
  python tools/create_update.py --version 2.8.8 --repo OWNER/REPOSITORY
Then commit/push the generated updates/ files. Devices check updates/latest.json.
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {'.git', '.venv', 'venv', '__pycache__', 'data', 'recordings', 'updates', 'node_modules', '.pytest_cache'}
EXCLUDED_FILES = {'.env', '.env.local'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', help='Version to publish (defaults to VERSION file)')
    parser.add_argument('--repo', default=os.environ.get('GITHUB_REPO', ''), help='GitHub owner/repository')
    parser.add_argument('--branch', default=os.environ.get('GITHUB_BRANCH', 'main'))
    parser.add_argument('--release-notes', default='', help='Short text shown in Settings')
    parser.add_argument('--output-dir', default='updates', help='Output folder, relative to project root by default')
    args = parser.parse_args()
    version = (args.version or (ROOT / 'VERSION').read_text(encoding='utf-8').strip()).strip().lstrip('vV')
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?', version):
        parser.error('Version must look like 2.8.8 (optional prerelease suffix supported).')
    if not args.repo or '/' not in args.repo.strip('/'):
        parser.error('Provide --repo OWNER/REPOSITORY (or set GITHUB_REPO).')
    repo = args.repo.strip('/')
    out = Path(args.output_dir)
    if not out.is_absolute(): out = ROOT / out
    out.mkdir(parents=True, exist_ok=True)
    # Ensure the version embedded in the package matches the published package.
    (ROOT / 'VERSION').write_text(version + '\n', encoding='utf-8')
    package_name = f'cricket-live-v{version}.zip'
    package_path = out / package_name
    with zipfile.ZipFile(package_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(ROOT.rglob('*')):
            rel = path.relative_to(ROOT)
            if any(part in EXCLUDED_DIRS for part in rel.parts) or path.name in EXCLUDED_FILES:
                continue
            if not path.is_file(): continue
            # Do not package the update builder's output or local runtime/cache artifacts.
            if path.suffix in {'.pyc', '.pyo'} or path.name.endswith(('-wal', '-shm')): continue
            archive.write(path, rel.as_posix())
    digest = hashlib.sha256(package_path.read_bytes()).hexdigest()
    (out / f'{package_name}.sha256').write_text(f'{digest}  {package_name}\n', encoding='utf-8')
    raw_base = f'https://raw.githubusercontent.com/{repo}/{args.branch}/updates'
    manifest = {
        'version': version,
        'package': package_name,
        'package_url': f'{raw_base}/{package_name}',
        'sha256': digest,
        'release_notes': args.release_notes,
    }
    (out / 'latest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(f'Created: {package_path}')
    print(f'Created: {out / (package_name + ".sha256")}')
    print(f'Created: {out / "latest.json"}')
    print(f'Version: {version}; SHA-256: {digest}')
    print('Next: commit and push VERSION and the generated updates/ files to the configured branch.')

if __name__ == '__main__': main()
