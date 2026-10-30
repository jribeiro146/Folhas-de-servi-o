"""Read-only history age simulation; no deletion or operational queue factory."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _read(path, kind):
    from src.services.sqlite_storage_format import validate_store
    db = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        validate_store(db, kind)
        return db
    except BaseException:
        db.close()
        raise


def simulate(queue_path, *, editing_path=None, days=(90,180,365), now=None):
    now = time.time() if now is None else now
    days = sorted(set(days))
    if (isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now) or now <= 0
            or not days or any(type(d) is not int or not 1 <= d <= 36500 for d in days)):
        raise ValueError('Invalid simulation time or periods')
    report = {'mode':'read_only_simulation','as_of':now,'periods_days':days,
              'deletion_candidates':0,'protected_job_ids':0,
              'protected_reasons':{},'plain_uploads_older_than':{str(d):0 for d in days},
              'plain_upload_json_bytes_older_than':{str(d):0 for d in days}}
    # Every job ID is an idempotency record. Age is not permission to delete it.
    # Only simple completed uploads are counted for a future archival study;
    # recipients, receipts, parent/child links and unknown shapes are excluded.
    with closing(_read(queue_path,'queue')) as db:
        for row in db.execute('SELECT * FROM graph_sync_jobs'):
            report['protected_job_ids'] += 1
            reason = 'other_history'
            try:
                payload = json.loads(row['payload_json'])
                result = json.loads(row['result_json']) if row['result_json'] is not None else {}
                if not isinstance(payload,dict) or not isinstance(result,dict):
                    raise ValueError('Invalid object')
                stamp = row['updated_at']
                if isinstance(stamp,bool) or not isinstance(stamp,(int,float)) or not math.isfinite(stamp) or not 0 <= stamp <= now:
                    raise ValueError('Invalid timestamp')
                if row['status'] != 'complete':
                    reason = 'unfinished_or_failed'
                elif (row['kind'] != 'upload_active' or set(result) != {'uploaded_files'}
                        or not isinstance(result['uploaded_files'],list)
                        or any(not isinstance(name,str) for name in result['uploaded_files'])
                        or not payload.get('draft_path') or not isinstance(payload['draft_path'],str)
                        or not set(payload) <= {'draft_path','_original_draft_path','_staging_dir','fail_if_exists','_job_owner'}):
                    reason = 'receipts_dependencies_or_unknown_shape'
                else:
                    reason = 'plain_upload_idempotency'
                    for period in days:
                        if stamp < now-period*86400:
                            report['plain_uploads_older_than'][str(period)] += 1
                            report['plain_upload_json_bytes_older_than'][str(period)] += len(row['payload_json'].encode('utf-8'))+len(row['result_json'].encode('utf-8'))
            except (ValueError,TypeError,OverflowError):
                reason = 'malformed_or_future'
            report['protected_reasons'][reason] = report['protected_reasons'].get(reason,0)+1
        mail = db.execute('SELECT COUNT(*),COALESCE(SUM(length(payload)),0),'
                          'COUNT(CASE WHEN payload IS NULL AND retired_at IS NOT NULL THEN 1 END) FROM graph_mail_messages').fetchone()
        report['mail_snapshots'] = {'protected_rows':mail[0],'protected_blob_bytes':mail[1],'already_retired_rows':mail[2]}
    if editing_path is not None:
        with closing(_read(editing_path,'editing')) as db:
            report['audit'] = {'protected_rows':db.execute('SELECT COUNT(*) FROM admin_actions').fetchone()[0],
                'older_than':{str(d):db.execute('SELECT COUNT(*) FROM admin_actions WHERE created_at>=0 AND created_at<?',
                                              (now-d*86400,)).fetchone()[0] for d in days},
                'deletion_candidates':0}
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue',type=Path,required=True,help='Explicit prepared SQLite snapshot; opened mode=ro')
    parser.add_argument('--editing',type=Path,help='Optional editing/audit snapshot, independently read')
    parser.add_argument('--days',type=int,nargs='+',default=[90,180,365])
    args = parser.parse_args(argv)
    from src.services.sqlite_storage_format import StorageFormatError
    try:
        report = simulate(args.queue,editing_path=args.editing,days=args.days)
    except (ValueError,OSError,sqlite3.Error,StorageFormatError) as exc:
        parser.exit(2,type(exc).__name__+': simulation refused\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
