"""Finite synthetic history measurements. Never accepts an existing database."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import platform
import statistics
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def seed(queue, count, profile, now):
    """Completed synthetic uploads, optionally accepted mail/Teams pairs."""
    with closing(queue._connect()) as db:
        db.execute('BEGIN')
        for i in range(count):
            age = (i % 730 + 1) * 86400
            identifier = f'upload-{i}'
            kind = 'upload_active'
            payload = {'draft_path':str(queue.database_path.parent / 'draft.xlsx')}
            result = {'uploaded_files':['draft.xlsx']}
            if profile == 'mixed' and i % 4 == 2:
                identifier, kind = f'mail-{i}', 'archive_and_remove'
                payload = {'mail':{'recipient':'customer@example.invalid'}, 'teams':{'title':'Fictício'}}
                result = {'phase':'mail_accepted','mail':{'accepted':True,'message_id':f'synthetic-{i}'}}
                db.execute('INSERT INTO graph_mail_messages VALUES (?,?,?,?,?,?,?,?,?)',
                    (identifier,None,'sender@example.invalid','synthetic','', 'a'*64,1024,'synthetic',now-age))
            elif profile == 'mixed' and i % 4 == 3:
                identifier, kind = f'teams:mail-{i-1}', 'notify_teams'
                payload = {'parent_job_id':f'mail-{i-1}', 'notification':{'title':'Fictício'}}
                result = {'phase':'teams_accepted','accepted':True}
            db.execute('INSERT INTO graph_sync_jobs '
                '(id,kind,payload_json,status,attempts,next_attempt_at,retryable,result_json,created_at,updated_at) '
                'VALUES (?,?,?,\'complete\',1,0,0,?,?,?)',
                (identifier,kind,json.dumps(payload),json.dumps(result),now-age,now-age))
        db.commit()


def measure(action, samples):
    # Warm the same DB/pages first; these are steady-state, not cold-start timings.
    action()
    wall, cpu = [], []
    for _ in range(samples):
        start, process = time.perf_counter(), time.process_time()
        action()
        wall.append((time.perf_counter()-start)*1000)
        cpu.append((time.process_time()-process)*1000)
    return {'wall_ms':wall,'median_wall_ms':statistics.median(wall),
            'cpu_ms':cpu,'median_cpu_ms':statistics.median(cpu)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sizes',type=int,nargs='+',default=[10000,100000])
    parser.add_argument('--samples',type=int,default=3)
    args = parser.parse_args(argv)
    if any(n not in (10000,100000) for n in args.sizes) or not 1 <= args.samples <= 10:
        parser.error('Sizes must be 10000/100000 and samples 1..10')
    from tools.test_runtime import configure, block_outbound_network
    root = configure()
    block_outbound_network()
    from src.services.graph_sync_queue import GraphSyncQueue
    from src.services.release_identity import get_release_info
    from src.queue_worker import process_once
    from src.services.sqlite_storage_format import initialize_store
    from tools.history_retention import simulate
    output = ROOT / 'output/history' / root.name
    output.mkdir(parents=True)
    report = {'status':'RUNNING','synthetic':True,'runtime':str(root),'source':get_release_info(),
              'expected_runs':2*len(args.sizes),'samples':args.samples,
              'python':platform.python_version(),'platform':platform.platform(),'runs':[]}
    now = time.time()
    for profile in ('uploads','mixed'):
        for count in args.sizes:
            queue = GraphSyncQueue(None,root / f'{profile}-{count}' / 'queue.sqlite3',auto_start=False)
            seed(queue,count,profile,now)
            audit = queue.database_path.parent / 'editing.sqlite3'
            with closing(sqlite3.connect(audit)) as db:
                initialize_store(db,'editing')
                db.executemany('INSERT INTO admin_actions (actor_email,action,target,reason,details_json,created_at) '
                    'VALUES (?,?,?,?,?,?)', [('admin@example.invalid','retry','synthetic','synthetic','{}',
                    now-(i%730+1)*86400) for i in range(count//10)])
                db.commit()
            def idle():
                result = process_once(queue)
                assert result['worker_acquired'] and result['before'] == result['after']
                assert result['after']['complete'] == count
                assert result['after']['teams_missing'] == result['after']['teams_conflicts'] == 0
            timings = {'summary':measure(queue.summary,args.samples),
                       'idle_cycle':measure(idle,args.samples),
                       'ten_status_reads':measure(lambda:[queue.status('upload-0') for _ in range(10)],args.samples)}
            with closing(queue._connect()) as db:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                size = db.execute('PRAGMA page_count').fetchone()[0]*db.execute('PRAGMA page_size').fetchone()[0]
            retention = simulate(queue.database_path,editing_path=audit,now=now)
            assert retention['deletion_candidates'] == 0 and retention['protected_job_ids'] == count
            assert retention['mail_snapshots']['protected_rows'] == (count//4 if profile == 'mixed' else 0)
            entry = {'profile':profile,'jobs':count,'database_bytes':size,'timings':timings,'retention':retention}
            report['runs'].append(entry)
            (output/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
            print(json.dumps(entry),flush=True)
    assert len(report['runs']) == report['expected_runs']
    report['status'] = 'PASS'
    (output/'result.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'status':'PASS','output':str(output)}),flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
