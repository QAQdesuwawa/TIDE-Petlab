"""Transactional physical prop state. No Actor state or model calls live here."""
import json,sqlite3,time,uuid
from contextlib import contextmanager
from pathlib import Path
from model_packages.desktop_pet_v1 import shared_meal as m

class MealHost:
    def __init__(self,path,participants):
        self._listeners=[]
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.participants=tuple(participants)
        if len(set(self.participants))!=2:raise ValueError('meal requires two distinct pets')
        with self._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS scene (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS effect (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            with db:yield db
        finally:db.close()
    def snapshot(self):
        with self._db() as db:row=db.execute('SELECT value FROM scene WHERE id=1').fetchone()
        if row is None:return None
        value=m.validate(json.loads(row[0]))
        if tuple(value['participants'])!=self.participants:raise ValueError('meal participants changed')
        return value
    def start(self,holder,*,interaction_id=None):
        if holder not in self.participants:raise ValueError('unknown meal holder')
        value=m.signed({'version':m.VERSION,'interaction_id':interaction_id or 'meal:'+uuid.uuid4().hex,'revision':0,
            'participants':list(self.participants),'holder_ref':holder,'portions':3,'request':None,'offer':None,'last_effect_ref':None})
        m.validate(value)
        with self._db() as db:db.execute('INSERT OR REPLACE INTO scene VALUES(1,?)',(json.dumps(value,ensure_ascii=False),))
        return value
    def check(self,binding):return m.check_current(binding,self.snapshot())
    def subscribe(self,listener):
        """Same-UI-thread committed prop projection, never another action."""
        self._listeners.append(listener)
    def finish(self,identity,binding,*,status,frames,reason=None):
        p=m.verify_binding(binding)
        if status not in ('completed','interrupted') or type(frames)is not int or frames<=0:raise ValueError('meal requires actual rendered effects')
        args={'binding_ref':p['fingerprint'],'status':status,'frames':frames,'reason':reason}
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT value FROM effect WHERE id=?',(identity,)).fetchone()
            if old:
                r=json.loads(old[0])
                if any(r[k]!=v for k,v in args.items()):raise ValueError('meal effect changed on replay')
                return r
            row=db.execute('SELECT value FROM scene WHERE id=1').fetchone()
            s=m.validate(json.loads(row[0])) if row else None
            if s is None:raise ValueError('meal scene unavailable')
            transferred=False;changed=False
            try:m.check_current(p,s);current=True
            except ValueError:
                if status=='completed':raise
                current=False
            next_state={k:v for k,v in s.items() if k!='fingerprint'}
            if current:
                if status=='completed':
                    key=p['key'];actor=p['actor_ref'];target=p['target_ref']
                    if key=='eat_rice':next_state['portions']-=1
                    elif key=='request_bowl':next_state['request']={'actor_ref':actor,'target_ref':target,'effect_ref':identity}
                    elif key=='offer_bowl':next_state['offer']={'actor_ref':actor,'target_ref':target,'effect_ref':identity}
                    elif key=='accept_bowl':next_state.update(holder_ref=actor,request=None,offer=None);transferred=True
                    elif key in ('keep_bowl','decline_bowl'):next_state.update(request=None,offer=None)
                    changed=True
                elif p['key'] in ('offer_bowl','accept_bowl','decline_bowl'):
                    next_state['offer']=None;changed=True
            if changed:next_state.update(revision=s['revision']+1,last_effect_ref=identity)
            after=m.signed(next_state);m.validate(after)
            result=m.signed({'version':m.VERSION,'effect_ref':identity,**args,'actor_ref':p['actor_ref'],'target_ref':p['target_ref'],
                'key':p['key'],'transferred':transferred,'holder_before':s['holder_ref'],'holder_after':after['holder_ref'],
                'scene_before':s['fingerprint'],'scene_after':after['fingerprint'],'at_ms':time.time_ns()//1_000_000})
            db.execute('INSERT INTO effect VALUES(?,?)',(identity,json.dumps(result,ensure_ascii=False)))
            db.execute('INSERT OR REPLACE INTO scene VALUES(1,?)',(json.dumps(after,ensure_ascii=False),))
        # Notify only after SQLite commit. A failed view is retried by the
        # existing scene refresh; it cannot roll back a completed transfer.
        if changed:
            for listener in tuple(self._listeners):
                try:listener(after)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Committed meal view refresh failed')
        return result
