"""Append-only execution evidence under the owning session's single-writer lease.

No event, state or History authority. A dispatched child without a receipt is
unknown, never permission to repeat an effect. Interrupted writes fail closed.
"""
import hashlib
import json
import os
import threading
from pathlib import Path
from framework.model_api import model_data_fingerprint,thaw_model_data,freeze_model_payload
from model_packages.desktop_pet_v1.composite import CompositePlan,ChildResult,summarize

_LOCK=threading.RLock()
VERSION='desktop-pet-parent-journal-v1'
BATCH_VERSION='desktop-pet-parent-journal-batch-v1'
CHILD_SOURCE_VERSION='desktop-pet-parent-child-reference-v1'


class ParentJournal:
    def __init__(self,root,parent_id):
        if not isinstance(parent_id,str) or not parent_id:raise ValueError('parent identity required')
        self.parent_id=parent_id
        self.directory=Path(root)/hashlib.sha256(parent_id.encode()).hexdigest()
        self._verified={}
        self._file_count=0

    def _read(self):
        state={'children':{},'closed':False};previous=None;records=[]
        paths=sorted(self.directory.glob('*.json'))
        for ordinal,path in enumerate(paths):
            try:raw=path.read_bytes()
            except (ValueError,OSError) as error:raise ValueError('parent journal needs reconciliation') from error
            if path.name!=f'{ordinal:06d}.json':raise ValueError('invalid parent journal lineage')
            cached=self._verified.get(path.name)
            if cached is not None and cached[0]==raw:
                rows=cached[1]
            else:
                try:value=json.loads(raw)
                except (ValueError,UnicodeError) as error:raise ValueError('parent journal needs reconciliation') from error
                if not isinstance(value,dict):raise ValueError('invalid parent journal record')
                if value.get('version')==BATCH_VERSION:
                    if (set(value)!={'version','records'} or not isinstance(value['records'],list)
                            or not value['records']):raise ValueError('invalid parent journal batch')
                    rows=value['records']
                else:rows=[value]
                for row in rows:
                    if (not isinstance(row,dict) or set(row)!={'version','parent_id','previous','operation','payload','fingerprint'}
                            or row['fingerprint']!=model_data_fingerprint({k:v for k,v in row.items() if k!='fingerprint'})):
                        raise ValueError('invalid parent journal lineage')
                rows=tuple(freeze_model_payload(row) for row in rows)
            # Cached rows are immutable and match bytes just read from disk.
            # Always bind their chain to this exact parent and preceding file.
            for row in rows:
                if row['parent_id']!=self.parent_id or row['version']!=VERSION or row['previous']!=previous:
                    raise ValueError('invalid parent journal lineage')
                # Public state retains the legacy mutable JSON shape. Never
                # expose the cached immutable payload (notably tuple vs list).
                self._apply(state,row['operation'],thaw_model_data(row['payload']))
                previous=row['fingerprint'];records.append(row)
            self._verified[path.name]=(raw,rows)
        self._file_count=len(paths)
        return state,records

    def _apply(self,state,operation,payload):
        if state['closed']:raise ValueError('parent is closed')
        if operation=='begin':
            if 'plan' in state:raise ValueError('parent already exists')
            plan=CompositePlan.create(payload['children'])
            if not payload.get('source'):raise ValueError('parent source evidence required')
            state.update(plan=plan,source=payload['source'])
            state['children']={key:{'phase':'planned'} for key in plan.children}
            return
        if 'plan' not in state:raise ValueError('parent has not begun')
        if operation=='outcome':
            from .execution import ExecutionOutcome
            from model_packages.desktop_pet_v1.composite import validate_outcome
            if 'outcome' in state:raise ValueError('outcome already sealed')
            outcome=ExecutionOutcome(**payload)
            validate_outcome(state['plan'],outcome)
            state['outcome']=payload
            return
        if operation=='close':
            if any(row['phase']!='finished' for row in state['children'].values()):
                raise ValueError('unresolved child effects require reconciliation')
            summarize(state['plan'],[ChildResult(key,**row['result']) for key,row in state['children'].items()])
            state['closed']=True;return
        child=payload.get('child_id')
        if child not in state['children']:raise ValueError('undeclared child')
        row=state['children'][child]
        if operation=='prepare':
            if row['phase']!='planned' or not payload.get('evidence'):raise ValueError('candidate already sealed or missing')
            evidence=payload['evidence']
            if evidence.get('version')==CHILD_SOURCE_VERSION:
                if (set(evidence)!={'version','content','source_fingerprint'}
                        or evidence['source_fingerprint']!=model_data_fingerprint(state['source'])
                        or evidence['content'].get('content_id')!=child):
                    raise ValueError('child evidence differs from parent source')
            row.update(phase='prepared',prepared=payload['evidence'])
        elif operation=='dispatch':
            if row['phase']!='prepared':raise ValueError('child may not dispatch or replay')
            row['phase']='dispatched'
        elif operation=='finish':
            if row['phase']=='finished':raise ValueError('child receipt already sealed')
            result=ChildResult(child,**payload['result'])
            if result.status in {'completed','interrupted','uncertain'} and row['phase']!='dispatched':
                raise ValueError('actual result lacks dispatched evidence')
            row.update(phase='finished',result=payload['result'])
        else:raise ValueError('unknown parent journal operation')

    def _append(self,operation,payload):
        self.append_many(((operation,payload),))

    def append_many(self,operations):
        """One durability barrier for consecutive steps without external effects.

        Logical v1 rows retain their order and fingerprints. Existing single-row
        files and partially completed old journals remain readable unchanged.
        """
        operations=tuple(operations)
        if not operations:raise ValueError('invalid parent publication batch')
        with _LOCK:
            state,records=self._read()
            previous=records[-1]['fingerprint'] if records else None
            rows=[]
            for operation,payload in operations:
                payload=thaw_model_data(payload)
                self._apply(state,operation,payload)
                row={'version':VERSION,'parent_id':self.parent_id,'previous':previous,
                     'operation':operation,'payload':payload}
                row['fingerprint']=model_data_fingerprint(row)
                rows.append(row);previous=row['fingerprint']
            value=rows[0] if len(rows)==1 else {'version':BATCH_VERSION,'records':rows}
            raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf8')
            self.directory.mkdir(parents=True,exist_ok=True)
            path=self.directory/f'{self._file_count:06d}.json'
            with path.open('xb') as stream:
                stream.write(raw);stream.flush();os.fsync(stream.fileno())
            self._verified[path.name]=(raw,tuple(freeze_model_payload(row) for row in rows))

    def begin(self,plan,source):self._append('begin',{'children':plan.children,'source':source})
    def begin_dispatch(self,plan,source,contents):
        source_ref=model_data_fingerprint(source)
        rows=[('begin',{'children':plan.children,'source':source})]
        for item in contents:
            rows.append(('prepare',{'child_id':item.content_id,'evidence':{
                'version':CHILD_SOURCE_VERSION,'content':item.to_record(),'source_fingerprint':source_ref}}))
            rows.append(('dispatch',{'child_id':item.content_id}))
        self.append_many(rows)
    def prepare(self,child_id,evidence):self._append('prepare',{'child_id':child_id,'evidence':evidence})
    def dispatch(self,child_id):self._append('dispatch',{'child_id':child_id})
    def finish(self,child_id,result):self._append('finish',{'child_id':child_id,'result':result})
    def close(self):self._append('close',{})
    def seal_outcome(self,outcome):self._append('outcome',outcome.to_record())
    def state(self):
        with _LOCK:return self._read()[0]
