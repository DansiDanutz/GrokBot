"""Bounded read-only retained history for cross-window lifecycle audits."""
import json
import os
import stat
from pathlib import Path
from datetime import datetime
from paper_grid import retention
from paper_grid.metric_constants import ARMS,EVIDENCE_MAX_BYTES,EVIDENCE_MAX_DAYS,SECONDS_PER_DAY


def _paths(runtime,doc,end):
    runtime=Path(runtime)
    if any(path.is_symlink() for path in (runtime,*runtime.parents)) or not runtime.is_dir():
        raise ValueError('audit metric runtime is missing or unsafe')
    start=retention._timestamp(doc['start_at']);retention._timestamp(end)
    if end<start or end-start>EVIDENCE_MAX_DAYS*SECONDS_PER_DAY:
        raise ValueError('audit metric history exceeds supported range')
    first,last=retention._day(start),retention._day(end)
    directory=retention._directory(runtime)
    paths=sorted(p for p in directory.iterdir() if retention.FILENAME.fullmatch(p.name)
                 and first<=p.stem<=last) if directory.exists() else []
    declared=doc.get('archive_files',[])
    if not isinstance(declared,list):
        raise ValueError('invalid declared archive files')
    available={p.name for p in paths}
    for name in declared:
        if not isinstance(name,str) or not retention.FILENAME.fullmatch(name):
            raise ValueError('invalid declared archive filename')
        if first<=name[:-5]<=last and name not in available:
            raise ValueError('required observation archive is missing or unsafe')
    if len(paths)>EVIDENCE_MAX_DAYS+1:
        raise ValueError('audit metric archive day limit exceeded')
    total=len(json.dumps(doc,allow_nan=False).encode())
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError('required observation archive is missing or unsafe')
        total+=path.stat().st_size
        if total>EVIDENCE_MAX_BYTES:
            raise ValueError('audit metric evidence size limit exceeded')
    if total>EVIDENCE_MAX_BYTES:
        raise ValueError('audit metric evidence size limit exceeded')
    return paths


def _archive(payload,path):
    archive=json.loads(payload)
    datetime.strptime(path.stem,'%Y-%m-%d')
    if not isinstance(archive,dict) or archive.get('schema')!=1 or archive.get('day')!=path.stem:
        raise ValueError('archive schema/day mismatch')
    result={}
    for kind in retention.RECORDS:
        rows=archive.get(kind,[] if kind=='errors' else None)
        if not isinstance(rows,list):
            raise ValueError('invalid archive records')
        for row in rows:
            retention._key(row)
            if retention._day(row['time'])!=path.stem:
                raise ValueError('archive record is in the wrong UTC day')
        result[kind]=retention._merge([],rows,kind)
    return result


def _read_archive(path,remaining):
    with os.fdopen(os.open(path,os.O_RDONLY|os.O_NOFOLLOW),'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError('audit metric archive is unsafe')
        payload=stream.read(remaining+1)
    if len(payload)>remaining:
        raise ValueError('audit metric evidence size limit exceeded')
    return _archive(payload,path),len(payload)


def load(runtime,doc,end):
    """Return a new document; preserve known pre-start account opens, never infer them."""
    paths=_paths(runtime,doc,end)
    rows={kind:[row for row in doc.get(kind,[]) if row['time']<=end]
          for kind in retention.RECORDS}
    consumed=len(json.dumps(doc,allow_nan=False).encode())
    for path in paths:
        archive,size=_read_archive(path,EVIDENCE_MAX_BYTES-consumed)
        consumed+=size
        for kind in retention.RECORDS:
            rows[kind].extend(row for row in archive[kind] if row['time']<=end)
    for arm in ARMS:
        for event in doc.get('accounts',{}).get(arm,{}).get('events',[]):
            if event['time']<=end:
                rows['events'].append(dict(event,account=arm))
    merged={kind:retention._merge([],records,kind) for kind,records in rows.items()}
    identities={}
    for event in merged['events']:
        identity=(event['time'],event.get('account'),event.get('type'),event.get('symbol'))
        encoded=retention._key(event)
        if identity in identities and identities[identity]!=encoded:
            raise ValueError('conflicting metric events')
        identities[identity]=encoded
    return dict(doc,**merged)
