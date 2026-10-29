"""Default local address provider. Owns processes/cache, never history or state.

One background worker, no request backlog, bounded foreground wait. Cache rows
are immutable document vectors, not copies of historical conversations.
"""
from pathlib import Path
from array import array
from collections import OrderedDict
from copy import deepcopy
import hashlib,json,math,queue,socket,sqlite3,subprocess,threading,time,urllib.request,urllib.error
from model_packages.desktop_pet_v1.rag_policy import MODEL_SHA256,RERANKER_SHA256,EMBEDDING_POLICY,POLICY,VERSION,select_scores

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(2**20),b''):h.update(block)
    return h.hexdigest()

def normalize(raw):
    while isinstance(raw,list) and len(raw)==1 and isinstance(raw[0],list):raw=raw[0]
    if not isinstance(raw,list) or len(raw)<256:raise ValueError('local embedding dimension mismatch')
    values=[float(x) for x in raw[:256]]
    if any(not math.isfinite(x) for x in values):raise ValueError('nonfinite local embedding')
    norm=math.sqrt(sum(x*x for x in values))
    if norm<=0:raise ValueError('empty local embedding')
    return array('f',(x/norm for x in values))

class LocalModelRequestError(OSError):
    def __init__(self,kind,path,status,detail):
        super().__init__('local model request failed')
        self.diagnostic={'stage':kind,'operation':path,'http_status':status,
            'input_limit_exceeded':('exceeds' in detail and ('context' in detail or 'batch' in detail))}

class LlamaEmbeddingEngine:
    def __init__(self,root,stop,log_path,kind="embedding"):
        self.process=None;self.stop=stop;self.kind=kind
        root=Path(root).resolve();manifest=json.loads((root/'MODEL_MANIFEST.json').read_text(encoding='utf8'))
        model=next(x for x in manifest['models'] if x['kind']==kind)
        expected=MODEL_SHA256 if kind=='embedding' else RERANKER_SHA256
        if model['sha256']!=expected:raise ValueError('wrong local embedding model')
        for row in manifest['files']:
            path=(root/row['path']).resolve()
            if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size!=row['bytes']:raise ValueError('local model resource missing')
            if row['path'].startswith('runtime/') or row['path']==model['path']:
                if sha(path)!=row['sha256']:raise ValueError('local model resource hash differs')
        weight=(root/model['path']).resolve()
        if not weight.is_relative_to(root) or sha(weight)!=expected:raise ValueError('local model hash differs')
        with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
        self.host=f'http://127.0.0.1:{port}';self.http=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        args=['--embedding','--pooling','last','--embd-normalize','2'] if kind=='embedding' else ['--embedding','--rerank','--pooling','rank']
        command=[str(root/'runtime/llama-server.exe'),'-m',str(weight),*args,'-c',('2048' if kind=='embedding' else '6144'),'--parallel',('1' if kind=='embedding' else '3'),'-t',('4' if kind=='embedding' else '8'),'-ub',('512' if kind=='embedding' else '1024'),'--host','127.0.0.1','--port',str(port)]
        Path(log_path).parent.mkdir(parents=True,exist_ok=True)
        with Path(log_path).open('ab') as log:
            self.process=subprocess.Popen(command,cwd=root/'runtime',stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+45
            while True:
                if stop.is_set():raise RuntimeError('local model warmup cancelled')
                if self.process.poll() is not None:raise RuntimeError('local model exited during warmup')
                try:
                    if self.request('/health').get('status')=='ok':break
                except OSError:pass
                if time.monotonic()>=deadline:raise TimeoutError('local model startup timeout')
                stop.wait(.1)
        except BaseException:
            self.close();raise
    def request(self,path,data=None):
        body=None if data is None else json.dumps(data,ensure_ascii=False).encode('utf8')
        req=urllib.request.Request(self.host+path,data=body,headers={'Content-Type':'application/json'})
        try:
            with self.http.open(req,timeout=10) as r:return json.loads(r.read())
        except urllib.error.HTTPError as exc:
            # Bounded local diagnostics; no input text, key, or remote URL.
            detail=exc.read(4096).decode('utf8',errors='replace')
            raise LocalModelRequestError(self.kind,path,exc.code,detail) from exc
    def embed(self,text):
        rows=self.request('/v1/embeddings',{'input':[text]})['data']
        if len(rows)!=1 or rows[0]['index']!=0:raise ValueError('local vector response mismatch')
        return normalize(rows[0]['embedding'])
    def rerank(self,query,documents):
        result=self.request('/v1/rerank',{'query':query,'documents':documents})
        rows=result.get('results',result.get('data'))
        if not isinstance(rows,list) or len(rows)!=len(documents):raise ValueError('missing rerank scores')
        scores={row['index']:row.get('relevance_score',row.get('score')) for row in rows}
        if set(scores)!=set(range(len(documents))) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in scores.values()):raise ValueError('invalid rerank score set')
        return [scores[i] for i in range(len(documents))]
    def close(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)

class LocalMemoryRetrieval:
    identity='petlab.local-qwen3-cpu-address-v1'
    model=MODEL_SHA256+':'+RERANKER_SHA256
    policy=POLICY
    def __init__(self,*,model_root,index_path,engine_factory=None):
        self.model_root=Path(model_root);self.index_path=Path(index_path)
        self._stop=threading.Event();self.ready=threading.Event();self._lock=threading.Lock()
        self._queue=queue.Queue(maxsize=1);self._deferred=None;self._busy=False;self._engine=None;self._ranker=None;self.error=None
        self._query_vectors=OrderedDict();self._score_cache=OrderedDict()
        self._vectors={};self._factory=engine_factory;self.vector_budget_s=.5;self.work_budget_s=1.5
        self.status='warming' if engine_factory or (self.model_root/'MODEL_MANIFEST.json').is_file() else 'unavailable'
        self._thread=None
        if self.status!='unavailable':
            self._thread=threading.Thread(target=self._run,name='pet-local-retrieval',daemon=True);self._thread.start()
    def _run(self):
        db=None
        try:
            self.index_path.parent.mkdir(parents=True,exist_ok=True)
            self._engine=(self._factory() if self._factory else LlamaEmbeddingEngine(self.model_root,self._stop,self.index_path.with_suffix('.model.log')))
            self._ranker=(self._engine if self._factory else LlamaEmbeddingEngine(self.model_root,self._stop,self.index_path.with_suffix('.reranker.log'),kind='reranker'))
            db=sqlite3.connect(self.index_path)
            db.execute('CREATE TABLE IF NOT EXISTS vectors(namespace TEXT,key TEXT,vector BLOB,PRIMARY KEY(namespace,key))')
            self.status='ready';self.ready.set()
            while not self._stop.is_set():
                try:job=self._queue.get(timeout=.1)
                except queue.Empty:continue
                try:job['result']=self._score(db,job)
                except Exception as exc:job['result']={'status':'failed','reason':type(exc).__name__,'selected':{},'model':self.model,'policy':dict(self.policy),**({'diagnostic':exc.diagnostic} if isinstance(exc,LocalModelRequestError) else {})}
                finally:
                    with self._lock:
                        successor=self._deferred;self._deferred=None
                        if successor is not None and not self._stop.is_set():self._queue.put_nowait(successor)
                        else:
                            self._busy=False
                            if successor is not None:self._finish_waiting(successor,'closed_before_start')
                    job['done'].set()
        except Exception as exc:self.status='failed';self.error=type(exc).__name__;self.ready.set()
        finally:
            if db is not None:db.close()
            if self._engine is not None:self._engine.close()
            if self._ranker is not None and self._ranker is not self._engine:self._ranker.close()
    def _score(self,db,job):
        started=time.perf_counter()
        namespace=hashlib.sha256((job['namespace']+'\0'+self.model+'\0qwen-mrl256-l2-v1').encode('utf8')).hexdigest()
        query=None;query_hit=False;query_key=None
        if not job.get('prime'):
            query_key=(self.identity,namespace,hashlib.sha256(job['query'].encode('utf8')).hexdigest())
            query=self._query_vectors.get(query_key);query_hit=query is not None
            if query_hit:self._query_vectors.move_to_end(query_key)
            else:
                query=tuple(self._engine.embed('Instruct: Retrieve committed events relevant to the current request, including paraphrases and later corrections.\nQuery: '+job['query']))
                self._query_vectors[query_key]=query
                while len(self._query_vectors)>128:self._query_vectors.popitem(last=False)
        query_ms=round((time.perf_counter()-started)*1000);vectors_started=time.perf_counter()
        scores={};hits=0;new=0
        for row in reversed(job['documents']):
            if time.perf_counter()-started>=self.vector_budget_s:break
            if job.get('prime'):
                with self._lock:
                    if self._deferred is not None:break
            if self._stop.is_set():raise RuntimeError('retrieval stopped')
            key=(namespace,row['content_fingerprint']);vector=self._vectors.get(key)
            if vector is None:
                saved=db.execute('SELECT vector FROM vectors WHERE namespace=? AND key=?',key).fetchone()
                if saved is not None:
                    vector=array('f');vector.frombytes(saved[0])
                    if len(vector)!=256 or any(not math.isfinite(x) for x in vector):raise ValueError('invalid saved vector')
                    hits+=1
                else:
                    vector=self._engine.embed(row['text']);new+=1
                    db.execute('INSERT INTO vectors VALUES(?,?,?)',(*key,vector.tobytes()))
                    db.commit()
                self._vectors[key]=vector
            else:hits+=1
            if query is not None:scores[row['event_id']]=max(-1.,min(1.,sum(a*b for a,b in zip(query,vector))))
        if job.get('prime'):return {'status':'indexed','new_vectors':new,'cached_vectors':hits,'unexamined_count':len(job['documents'])-hits-new}
        vectors_ms=round((time.perf_counter()-vectors_started)*1000);rank_started=time.perf_counter()
        coarse=select_scores(scores,EMBEDDING_POLICY)
        by_id={row['event_id']:row for row in job['documents']}
        qualified=[by_id[i] for i in coarse]
        final={};rank_hits=0;rank_computed=0
        policy_key=hashlib.sha256(json.dumps(dict(self.policy),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        def pair_key(row):return (*query_key,policy_key,row['content_fingerprint'],hashlib.sha256(row['text'].encode('utf8')).hexdigest())
        # Bounded computational caches, never a cap on candidate selection.
        for row in qualified:
            key=pair_key(row)
            if key in self._score_cache:
                final[row['event_id']]=self._score_cache[key];rank_hits+=1;self._score_cache.move_to_end(key)
        def publish():
            selected=select_scores(final,self.policy)
            snapshot={'status':'available','selected':{i:final[i] for i in selected},'model':self.model,'provider':self.identity,
                'policy':dict(self.policy),'candidate_count':len(scores),'cached_vectors':hits,'new_vectors':new,
                'best_similarity':max(final.values()) if final else None,'embedding_qualified_count':len(qualified),
                'coverage':{'total_documents':len(job['documents']),'vector_examined':len(scores),'rank_examined':len(final),
                    'rank_unexamined':len(qualified)-len(final),'vector_unexamined':len(job['documents'])-len(scores),'unexamined_is_negative':False},
                'scoring':'embedding-qualified-reranker-v1',
                'compute_reuse':{'version':'exact-source-query-pair-lru-v1','query_vector_hit':query_hit,'rerank_hits':rank_hits,'rerank_computed':rank_computed},
                'timings_ms':{'query_embedding':query_ms,'document_vectors':vectors_ms,'rerank':round((time.perf_counter()-rank_started)*1000),
                    'total':round((time.perf_counter()-started)*1000)},'state_commit_authority':False}
            # Each publication owns its dictionaries; never update a delivered prefix.
            if final:job['progress']=snapshot
            return snapshot
        if final:publish()
        # Three is the deployed server's physical slot count, not a recall cap.
        for first in range(0,len(qualified),3):
            if time.perf_counter()-started>=self.work_budget_s:break
            batch=[row for row in qualified[first:first+3] if row['event_id'] not in final]
            if not batch:continue
            ranked=self._ranker.rerank(job['query'],[row['text'] for row in batch])
            if len(ranked)!=len(batch) or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in ranked):
                raise ValueError('invalid exact rerank batch')
            for row,value in zip(batch,ranked):
                final[row['event_id']]=value;self._score_cache[pair_key(row)]=value
                while len(self._score_cache)>2048:self._score_cache.popitem(last=False)
            rank_computed+=len(batch);publish()
        return publish()
    def submit(self,*,query,documents,namespace):
        job={'query':query,'documents':documents,'namespace':namespace,'done':threading.Event()}
        immediate=None
        if self.status!='ready':immediate={'status':self.status,'reason':self.error or self.status,'selected':{},'model':self.model,'policy':dict(self.policy)}
        elif len({r['event_id'] for r in documents})!=len(documents):raise ValueError('duplicate local retrieval candidates')
        else:
            with self._lock:
                if self._busy or self._stop.is_set():immediate={'status':'busy','selected':{},'model':self.model,'policy':dict(self.policy)}
                else:self._busy=True;self._queue.put_nowait(job)
        if immediate is not None:job['result']=immediate;job['done'].set()
        return job
    def _finish_waiting(self,job,reason):
        job['result']={'status':'busy','reason':reason,'selected':{},'model':self.model,'policy':dict(self.policy)}
        job['done'].set()

    def submit_latest(self,*,query,documents,namespace):
        """One replaceable waiting query; existing delivered snapshots stay immutable."""
        if self.status!='ready':return self.submit(query=query,documents=documents,namespace=namespace)
        if len({r['event_id'] for r in documents})!=len(documents):raise ValueError('duplicate local retrieval candidates')
        job={'query':query,'documents':documents,'namespace':namespace,'done':threading.Event()}
        with self._lock:
            if self._stop.is_set():self._finish_waiting(job,'closed_before_start')
            elif self._busy:
                if self._deferred is not None:self._finish_waiting(self._deferred,'superseded_before_start')
                self._deferred=job
            else:self._busy=True;self._queue.put_nowait(job)
        return job

    def result(self,job,*,timeout_s=.5):
        if not job['done'].is_set():
            progress=job.get('progress')
            if progress is not None and progress['selected']:
                return {**deepcopy(progress),'publication':'foreground-ready-snapshot-v1'}
        if not job['done'].wait(timeout_s):
            progress=job.get('progress')
            if progress is not None:
                return {**deepcopy(progress),'publication':'foreground-deadline-snapshot-v1'}
            return {'status':'timeout','selected':{},'model':self.model,'policy':dict(self.policy)}
        return job['result']
    def retrieve(self,*,query,documents,namespace,timeout_s=.5):
        return self.result(self.submit(query=query,documents=documents,namespace=namespace),timeout_s=timeout_s)
    def prime(self,*,documents,namespace):
        if self.status!='ready':return False
        with self._lock:
            if self._busy or self._stop.is_set():return False
            self._busy=True;self._queue.put_nowait({'documents':documents,'namespace':namespace,'prime':True,'done':threading.Event()})
        return True
    def close(self):
        self._stop.set()
        with self._lock:
            if self._deferred is not None:self._finish_waiting(self._deferred,'closed_before_start');self._deferred=None
            try:queued=self._queue.get_nowait()
            except queue.Empty:pass
            else:self._finish_waiting(queued,'closed_before_start')
        if self._engine is not None:self._engine.close()
        if self._ranker is not None and self._ranker is not self._engine:self._ranker.close()
        if self._thread is not None:self._thread.join(3)
        if self._thread is not None and self._thread.is_alive():raise RuntimeError('local retrieval worker did not close')
