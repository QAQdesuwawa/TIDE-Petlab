"""Transactional physical prop state. No Actor state or model calls live here."""
import json,sqlite3,time,uuid
from contextlib import contextmanager
from pathlib import Path
from model_packages.desktop_pet_v1 import shared_meal as m

class MealHost:
    def __init__(self,path,participants):
        self._listeners=[]
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.participants=tuple(participants)
        if not 2<=len(self.participants)<=16 or len(set(self.participants))!=len(self.participants) or any(not isinstance(p,str) or not p for p in self.participants):raise ValueError('invalid distinct meal participants')
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='bowls'").fetchone() is None:
                db.execute('CREATE TABLE bowls (id TEXT PRIMARY KEY, holder TEXT UNIQUE NOT NULL, value TEXT NOT NULL)')
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='scene'").fetchone() is not None:
                    old=db.execute('SELECT value FROM scene WHERE id=1').fetchone()
                    if old is not None:
                        bowl=self._decode(old[0])
                        db.execute('INSERT INTO bowls VALUES(?,?,?)',(bowl['interaction_id'],bowl['holder_ref'],old[0]))
            db.execute('CREATE TABLE IF NOT EXISTS effect (id TEXT PRIMARY KEY, value TEXT NOT NULL)')
    @contextmanager
    def _db(self):
        db=sqlite3.connect(self.path,timeout=5)
        try:
            with db:yield db
        finally:db.close()
    def _decode(self,raw):
        value=m.validate(json.loads(raw))
        if tuple(value['participants'])!=self.participants:raise ValueError('meal participants changed')
        return value

    def snapshots(self):
        with self._db() as db:
            return tuple(self._decode(raw) for raw, in db.execute('SELECT value FROM bowls ORDER BY rowid'))

    def snapshot(self):
        # Compatibility accessor for the original single-bowl callers.
        rows=self.snapshots()
        return rows[-1] if rows else None

    def start(self,holder,*,interaction_id=None):
        if holder not in self.participants:raise ValueError('unknown meal holder')
        value=m.signed({'version':m.VERSION if len(self.participants)==2 else m.MULTI_VERSION,'interaction_id':interaction_id or 'meal:'+uuid.uuid4().hex,'revision':0,
            'participants':list(self.participants),'holder_ref':holder,'portions':3,'request':None,'offer':None,'last_effect_ref':None})
        m.validate(value)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM bowls WHERE holder=?',(holder,))
            db.execute('INSERT INTO bowls VALUES(?,?,?)',(value['interaction_id'],holder,json.dumps(value,ensure_ascii=False)))
        return value

    def _bound_bowl(self,db,binding):
        row=db.execute('SELECT value FROM bowls WHERE id=?',(binding['scene']['interaction_id'],)).fetchone()
        return None if row is None else self._decode(row[0])

    def _check(self,db,binding,bowl):
        result=m.check_current(binding,bowl)
        if binding['key'] in ('offer_bowl','accept_bowl'):
            recipient=binding['target_ref'] if binding['key']=='offer_bowl' else binding['actor_ref']
            other=db.execute('SELECT value FROM bowls WHERE holder=?',(recipient,)).fetchone()
            if other and self._decode(other[0])['portions']:
                raise ValueError('meal recipient already holds another bowl')
        return result

    def check(self,binding):
        binding=m.verify_binding(binding)
        with self._db() as db:
            bowl=self._bound_bowl(db,binding)
            if bowl is None:raise ValueError('meal scene replaced')
            return self._check(db,binding,bowl)

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
            bowl=self._bound_bowl(db,p)
            s=bowl if bowl is not None else p['scene']
            transferred=False;changed=False
            try:
                if bowl is None:raise ValueError('meal scene replaced')
                self._check(db,p,s)
                current=True
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
            result=m.signed({'version':p['version'],'effect_ref':identity,**args,'actor_ref':p['actor_ref'],'target_ref':p['target_ref'],
                'key':p['key'],'interaction_id':s['interaction_id'],'transferred':transferred,'holder_before':s['holder_ref'],'holder_after':after['holder_ref'],
                'scene_before':s['fingerprint'],'scene_after':after['fingerprint'],'at_ms':time.time_ns()//1_000_000})
            db.execute('INSERT INTO effect VALUES(?,?)',(identity,json.dumps(result,ensure_ascii=False)))
            if changed:
                if transferred:
                    db.execute('DELETE FROM bowls WHERE holder=?',(after['holder_ref'],))
                db.execute('UPDATE bowls SET holder=?,value=? WHERE id=?',
                           (after['holder_ref'],json.dumps(after,ensure_ascii=False),s['interaction_id']))
        # Notify only after SQLite commit. A failed view is retried by the
        # existing scene refresh; it cannot roll back a completed transfer.
        if changed:
            for listener in tuple(self._listeners):
                try:listener(after)
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Committed meal view refresh failed')
        return result
