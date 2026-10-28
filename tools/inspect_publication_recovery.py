"""Read only a consistent backup copy; do not import config or start the app."""
import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.services.editing_state_format import decode_editing_state, EditingStateError
from src.services.publication_evidence import legacy_fingerprint


def inspect(database, bundles):
    paths = {}
    for path in bundles.rglob('*.xlsx'):
        if path.is_symlink() or not path.resolve().is_relative_to(bundles):
            continue
        paths.setdefault(path.stem, []).append(path)
    counts = Counter()
    records = []
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as connection:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        for document_id, raw in connection.execute('SELECT document_id,state_json FROM editing_states'):
            category = 'unchanged'
            recovery = False
            try:
                state = decode_editing_state(raw, document_id)
                recovery = isinstance(state.get('autosave'), dict)
                candidates = {p for alias in state['aliases'] for p in paths.get(alias, [])}
                if state.get('publication_journal'):
                    category = 'publication_local_pending'
                elif state.get('source_conflict'):
                    category = 'choice_pending'
                elif state['workspace_kind'] == 'private':
                    category = 'private_workspace'
                elif state.get('closed_by_operation'):
                    category = 'closed'
                elif len(candidates) != 1:
                    category = 'source_missing_or_ambiguous'
                elif state['fingerprint'] and legacy_fingerprint(next(iter(candidates))) != state['fingerprint']:
                    category = 'source_changed_requires_choice'
            except (EditingStateError, OSError, ValueError):
                category = 'requires_diagnosis'
            counts[category] += 1
            records.append({'document_ref': hashlib.sha256(document_id.encode()).hexdigest()[:16],
                            'classification': category, 'server_recovery_available': recovery})
    return {'read_only': True, 'scope': 'consistent_backup_copy', 'counts': dict(counts), 'records': records}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database-copy', type=Path, required=True)
    parser.add_argument('--bundles-copy', type=Path, required=True)
    args = parser.parse_args()
    for path in (args.database_copy, args.bundles_copy):
        if not path.is_absolute() or path.is_symlink() or str(path).startswith(('\\\\', '//')):
            parser.error('Indique caminhos absolutos para cópias locais consistentes.')
    print(json.dumps(inspect(args.database_copy.resolve(), args.bundles_copy.resolve()), ensure_ascii=False))


if __name__ == '__main__':
    main()
