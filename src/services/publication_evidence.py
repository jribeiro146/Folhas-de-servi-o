"""Exact local evidence for acknowledging our own Graph metadata publication.

The editing fingerprint remains the legacy stat-based value. These additional
hashes only prove a bounded local transition; they never rebase a remote edit.
"""
import hashlib
from pathlib import Path, PurePosixPath


def file_evidence(path: Path):
    if path.is_symlink():
        raise ValueError('Uma ligação não pode servir de prova de publicação.')
    try:
        before = path.stat()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        after = path.stat()
    except FileNotFoundError:
        return None
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError('O ficheiro mudou durante a verificação da publicação.')
    return {'sha256': digest, 'size': after.st_size, 'mtime_ns': after.st_mtime_ns}


def content_evidence(path: Path):
    return {p.name: file_evidence(p) for p in (
        path, path.with_name(path.stem + '__documento.json'),
        path.with_name(path.stem + '__assinatura_cliente.png'))}


def legacy_fingerprint(path: Path):
    parts = []
    for item in (path, path.with_name(path.stem + '__documento.json'),
                 path.with_name(path.stem + '__assinatura_cliente.png'),
                 path.with_name(path.name + '.graph.json')):
        try:
            stat = item.stat()
            parts.append(f'{item.name}:{stat.st_mtime_ns}:{stat.st_size}')
        except FileNotFoundError:
            parts.append(f'{item.name}:missing')
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()


def metadata_name(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value:
        return False
    path = PurePosixPath(value)
    return (not path.is_absolute() and all(p not in ('', '.', '..') for p in value.split('/'))
        and (path.name in ('.graph_bundle.json', '.fs-upload-owner.json')
             or path.name.endswith('.graph.json'))
        and path.name != '.fs-local-dirty.graph.json')


def evidence_valid(value):
    return value is None or (isinstance(value, dict)
        and set(value) == {'sha256', 'size', 'mtime_ns'}
        and isinstance(value['sha256'], str) and len(value['sha256']) == 64
        and all(c in '0123456789abcdef' for c in value['sha256'])
        and type(value['size']) is int and value['size'] >= 0
        and type(value['mtime_ns']) is int and value['mtime_ns'] >= 0)


def validate_journal(value):
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        return False
    if not all(isinstance(value.get(k), str) and value[k] for k in
               ('publication_id', 'source_name', 'before_fingerprint')):
        return False
    if '/' in value['source_name'] or '\\' in value['source_name']:
        return False
    content, files = value.get('content'), value.get('files')
    stem = Path(value['source_name']).stem
    if not isinstance(content, dict) or set(content) != {
        value['source_name'], stem + '__documento.json', stem + '__assinatura_cliente.png'}:
        return False
    if not all(evidence_valid(v) for v in content.values()):
        return False
    if not isinstance(files, dict) or not files or len(files) > 10000:
        return False
    return all(metadata_name(name) and isinstance(proof, dict)
        and set(proof) == {'before', 'after'} and evidence_valid(proof['before'])
        and proof['after'] is not None and evidence_valid(proof['after'])
        for name, proof in files.items())


def journal_position(path: Path, journal):
    """Never change files here: readers may hold only the SQLite transaction."""
    if not validate_journal(journal) or path.name != journal['source_name']:
        return 'diverged'
    if content_evidence(path) != journal['content']:
        return 'diverged'
    completed = True
    for name, proof in journal['files'].items():
        candidate = path.parent / name
        if not candidate.resolve().is_relative_to(path.parent.resolve()):
            return 'diverged'
        current = file_evidence(candidate)
        if current != proof['after']:
            completed = False
            if current != proof['before']:
                return 'diverged'
    return 'after' if completed else 'pending'
