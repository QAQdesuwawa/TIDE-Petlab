"""Persistent invitation effects; independent of Actor/root state."""
import json,sqlite3
from contextlib import contextmanager
from pathlib import Path
from model_packages.desktop_pet_v1 import social_contact as c

class ContactHost:
    def __init__(self,path,participants):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.participants=list(participants)
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS invitations(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS effects(id TEXT PRIMARY KEY,value TEXT NOT NULL)')
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            with db:yield db
        finally:db.close()
    def _snapshot(self,db):
        return c.validate(c.signed(dict(version=c.VERSION,participants=self.participants,
            invitations={k:json.loads(v) for k,v in db.execute('SELECT id,value FROM invitations')})))
    def snapshot(self):
        with self._db() as db:return self._snapshot(db)
    def check(self,binding):return c.check(binding,self.snapshot())
    def cancel_actor(self,actor):
        with self._db() as db:
            for identity,row in self._snapshot(db)['invitations'].items():
                if actor in (row['actor'],row['target']):db.execute('DELETE FROM invitations WHERE id=?',(identity,))
    def finish(self,identity,binding,*,status,frames):
        if status not in ('completed','interrupted') or type(frames)is not int or frames<=0:raise ValueError('contact actual frames missing')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT value FROM effects WHERE id=?',(identity,)).fetchone()
            args=dict(binding_ref=binding['fingerprint'],status=status,frames=frames)
            if old:
                result=json.loads(old[0])
                if any(result[k]!=v for k,v in args.items()):raise ValueError('contact replay differs')
                return result
            if status=='completed':c.check(binding,self._snapshot(db))
            b=binding;verb,kind=b['key'].split('_',1)
            if status=='completed' and verb=='invite':
                db.execute('INSERT INTO invitations VALUES(?,?)',(identity,json.dumps(dict(actor=b['actor'],target=b['target'],kind=kind))))
            elif b['invitation'] is not None:
                db.execute('DELETE FROM invitations WHERE id=?',(b['invitation'],))
            result=c.signed(dict(version=c.VERSION,effect_ref=identity,**args))
            db.execute('INSERT INTO effects VALUES(?,?)',(identity,json.dumps(result)))
            return result
