#!/usr/bin/env python3
"""Install the separately delivered Phase 2.2 Idle atlas without touching other states."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import zipfile

ASSET = 'static/public/assets/sprites/hyangdani-idle-v3-4x2.png'
MANIFEST = 'static/public/assets/sprites/manifest.json'
SHA256 = '878b44b6e41538061b6b4c794a5f942cf1bdd383bf4f6ce5ef4a802157543405'
SOURCE = {'type': 'atlas', 'file': Path(ASSET).name, 'columns': 4, 'rows': 2, 'width': 1536, 'height': 768}
ANCHOR = [0.5, 707 / 768]


def read_asset(bundle):
    if bundle.is_dir():
        return (bundle / ASSET).read_bytes()
    with zipfile.ZipFile(bundle) as archive:
        return archive.read(ASSET)


def validate_asset(data):
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError('Atlas SHA-256 mismatch')
    if data[:16] != b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR':
        raise ValueError('Not a PNG image')
    width, height, depth, color_type = struct.unpack('>IIBB', data[16:26])
    if (width, height, depth, color_type) != (1536, 768, 8, 6):
        raise ValueError('Expected 1536×768, 8-bit RGBA PNG')


def prepare_manifest(raw):
    manifest = json.loads(raw)
    if manifest.get('version') != 3 or manifest.get('character') != 'hyangdani':
        raise ValueError('Expected Hyangdani manifest v3')
    idle = manifest['states']['idle']
    if idle.get('frames') != 8 or idle.get('fps') != 8 or idle.get('loop') is not True:
        raise ValueError('Idle animation contract differs')
    if idle.get('source') not in (None, SOURCE):
        raise ValueError('Existing Idle source differs; refusing to overwrite')
    fallback = idle.get('fallback')
    if not isinstance(fallback, dict) or fallback.get('file') != 'hyangdani-idle.png':
        raise ValueError('Expected existing Idle fallback')
    idle.update(source=SOURCE, facing='right', anchor=ANCHOR)
    return (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def atomic_write(path, data):
    fd, name = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(name, 0o644)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install(repo, bundle, check=False):
    data = read_asset(bundle)
    validate_asset(data)
    target = repo / ASSET
    manifest = repo / MANIFEST
    raw = manifest.read_bytes()
    revised = prepare_manifest(raw)
    if not (target.parent / 'hyangdani-idle.png').is_file():
        raise ValueError('Existing fallback file missing')
    existed = target.exists()
    if existed and target.read_bytes() != data:
        raise ValueError('Existing atlas differs; refusing to overwrite')
    if check:
        return
    # Copy the new asset first; a failed manifest write leaves the old game intact.
    try:
        if not existed:
            atomic_write(target, data)
        if revised != raw:
            atomic_write(manifest, revised)
    except BaseException:
        if not existed and target.exists() and manifest.read_bytes() == raw:
            target.unlink()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--bundle', type=Path, required=True, help='Delivered ZIP or extracted directory')
    parser.add_argument('--check', action='store_true', help='Validate without modifying files')
    args = parser.parse_args()
    install(args.repo.resolve(), args.bundle.resolve(), args.check)
    print('Idle asset validation OK' if args.check else 'Idle atlas installed; other states unchanged')


if __name__ == '__main__':
    main()
