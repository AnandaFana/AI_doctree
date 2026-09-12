"""Read-only full content snapshot, excluding Git internals; never execute source."""
import argparse
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from doctree.scanner import file_hash, git_info, is_link, now, write_json, digest


def snapshot(root):
    root = root.resolve()
    files, links = {}, []
    for directory, dirs, names in os.walk(root, followlinks=False):
        directory = Path(directory)
        links.extend(str(directory / d) for d in dirs if is_link(directory / d))
        dirs[:] = sorted(d for d in dirs if d != '.git' and not is_link(directory / d))
        for name in sorted(names):
            path = directory / name
            if is_link(path):
                links.append(str(path))
                continue
            st = path.stat()
            files[path.relative_to(root).as_posix()] = {'sha256': file_hash(path), 'size': st.st_size,
                                                        'hardlink_count': st.st_nlink}
    return {'root': str(root), 'at': now(), 'git': git_info(root), 'files': files,
            'links': links, 'fingerprint': digest(files), 'file_count': len(files),
            'bytes': sum(f['size'] for f in files.values()),
            'scope': 'All non-Git regular files, including ignored binary outputs'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('root', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    result = snapshot(args.root)
    if args.compare:
        import json
        before = json.loads(args.compare.read_text(encoding='utf-8'))
        result['comparison'] = {'same_content': before['fingerprint'] == result['fingerprint'],
                                'same_git_status': before['git'] == result['git'],
                                'changed_paths': [p for p in sorted(before['files'].keys() | result['files'].keys())
                                                  if before['files'].get(p) != result['files'].get(p)]}
    write_json(args.output, result)
    print({k: v for k, v in result.items() if k != 'files'})
