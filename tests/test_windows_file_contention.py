"""Native Windows contention and bounded directory publication; synthetic data."""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.services.file_mutex import file_mutex,FileMutexBusy
from src.services.atomic_files import rename_local


@pytest.mark.skipif(os.name!='nt',reason='Native Windows byte-range lock')
@pytest.mark.parametrize('initial',[b'',b'0'])
def test_empty_or_legacy_mutex_contender_is_busy_and_recovers_after_holder_dies(tmp_path,initial):
    path,ready=tmp_path/'mutex',tmp_path/'ready'
    path.write_bytes(initial)
    code='''
import msvcrt,sys
from pathlib import Path
with Path(sys.argv[1]).open('a+b') as handle:
    handle.seek(0)
    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    Path(sys.argv[2]).write_text('ready')
    sys.stdin.read()
'''
    child=subprocess.Popen([sys.executable,'-B','-c',code,str(path),str(ready)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        deadline=time.monotonic()+10
        while not ready.exists():
            assert child.poll() is None and time.monotonic()<deadline
            time.sleep(.01)
        with pytest.raises(FileMutexBusy):
            with file_mutex(path): pytest.fail('Contender entered while another process owned the byte')
    finally:
        if child.poll() is None: child.kill()
        child.communicate(timeout=5)
    with pytest.raises(RuntimeError):
        with file_mutex(path): raise RuntimeError('Synthetic owner exception')
    with file_mutex(path):
        assert path.stat().st_size==len(initial)
    assert path.read_bytes()==initial


@pytest.mark.parametrize('winerror',[5,32,33])
def test_directory_rename_retries_same_operation_until_reader_releases(tmp_path,monkeypatch,winerror):
    source,target=tmp_path/'staged',tmp_path/'complete'
    source.mkdir()
    (source/'data').write_bytes(b'preserved')
    original=os.rename
    calls=[]
    delays=[]
    def denied_then_ok(a,b):
        calls.append((a,b))
        assert (source/'data').read_bytes()==b'preserved' and not target.exists()
        if len(calls)<3:
            problem=PermissionError('Synthetic reader lock')
            problem.winerror=winerror
            raise problem
        original(a,b)
    monkeypatch.setattr('src.services.atomic_files.os.rename',denied_then_ok)
    monkeypatch.setattr('src.services.atomic_files.time.sleep',delays.append)
    rename_local(source,target)
    assert len(calls)==3 and len(set(calls))==1 and delays==[.01,.02]
    assert (target/'data').read_bytes()==b'preserved' and not source.exists()


@pytest.mark.parametrize('winerror,count',[(5,8),(None,1),(2,1)])
def test_persistent_or_nonwindows_rename_denial_is_not_hidden(tmp_path,monkeypatch,winerror,count):
    source,target=tmp_path/'staged',tmp_path/'complete'
    source.mkdir()
    (source/'data').write_bytes(b'preserved')
    calls=[]
    delays=[]
    def denied(*args):
        calls.append(args)
        problem=PermissionError('Synthetic permanent denial')
        problem.winerror=winerror
        raise problem
    monkeypatch.setattr('src.services.atomic_files.os.rename',denied)
    monkeypatch.setattr('src.services.atomic_files.time.sleep',delays.append)
    with pytest.raises(PermissionError): rename_local(source,target)
    assert len(calls)==count and sum(delays)<=.4500001
    assert (source/'data').read_bytes()==b'preserved' and not target.exists()


@pytest.mark.skipif(os.name!='nt',reason='Native Windows open-reader rename denial')
def test_native_reader_can_release_between_atomic_rename_attempts(tmp_path,monkeypatch):
    source,target=tmp_path/'staged',tmp_path/'complete'
    source.mkdir()
    file=source/'synthetic.pdf'
    file.write_bytes(b'%PDF-synthetic')
    reader=file.open('rb')
    delays=[]
    def release(delay):
        delays.append(delay)
        reader.close()
    monkeypatch.setattr('src.services.atomic_files.time.sleep',release)
    try: rename_local(source,target)
    finally: reader.close()
    assert delays==[.01] and (target/file.name).read_bytes()==b'%PDF-synthetic'
