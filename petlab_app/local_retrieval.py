"""Default local address provider. Owns processes/cache, never history or state.

One background worker, no request backlog, bounded foreground wait. Cache rows
are immutable document vectors, not copies of historical conversations.
"""
from pathlib import Path
from array import array
from collections import OrderedDict
from copy import deepcopy
from itertools import chain,islice
import hashlib,json,math,queue,socket,sqlite3,subprocess,threading,time,urllib.request,urllib.error
from model_packages.desktop_pet_v1.rag_policy import MODEL_SHA256,RERANKER_SHA256,EMBEDDING_POLICY,POLICY,VERSION,select_scores
from .local_model_lifetime import spawn_owned_local_model as _spawn_owned_local_model

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
    def __init__(self,root,stop,log_path,kind="embedding",*,runtime=None):
        self.process=None;self._process_lifetime=None;self.stop=stop;self.kind=kind
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
        executable=(root/'runtime/llama-server.exe') if runtime is None else runtime[0]
        command=[str(executable),'-m',str(weight),*args,'-c',('2048' if kind=='embedding' else '6144'),'--parallel',('1' if kind=='embedding' else '3'),'-t',('4' if kind=='embedding' else '8'),'-ub',('512' if kind=='embedding' else '1024'),'--host','127.0.0.1','--port',str(port)]
        if runtime is not None:
            command.extend(('--device',runtime[1],'-ngl','99'))
        Path(log_path).parent.mkdir(parents=True,exist_ok=True)
        with Path(log_path).open('ab') as log:
            self.process,self._process_lifetime=_spawn_owned_local_model(
                command,cwd=executable.parent,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
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
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=2)
                except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=2)
        finally:
            if self._process_lifetime is not None:
                self._process_lifetime.close()

class LocalMemoryRetrieval:
    identity='petlab.local-qwen3-cpu-address-v1'
    model=MODEL_SHA256+':'+RERANKER_SHA256
    policy=POLICY
    def __init__(self,*,model_root,index_path,engine_factory=None,verified_runtime=None):
        self.model_root=Path(model_root);self.index_path=Path(index_path)
        self._runtime=None
        if verified_runtime is not None:
            executable,device,digest=verified_runtime
            executable=Path(executable).resolve(strict=True)
            if sha(executable)!=digest:
                raise ValueError('alternate retrieval runtime hash differs')
            self._runtime=(executable,device)
            self.identity=f'petlab.local-qwen3-address-v1:{digest}:{device}'
        self._stop=threading.Event();self.ready=threading.Event();self._lock=threading.Lock()
        self._queue=queue.Queue(maxsize=1);self._deferred=None;self._pending_primes=OrderedDict();self._busy=False;self._engine=None;self._ranker=None;self.error=None
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
            self._engine=(self._factory() if self._factory else LlamaEmbeddingEngine(self.model_root,self._stop,self.index_path.with_suffix('.model.log'),runtime=self._runtime))
            self._ranker=(self._engine if self._factory else LlamaEmbeddingEngine(self.model_root,self._stop,self.index_path.with_suffix('.reranker.log'),kind='reranker',runtime=self._runtime))
            if self._runtime is not None:
                # Complete lazy device initialization before advertising readiness.
                # These fixed strings never enter the query or document caches.
                self._engine.embed('Local retrieval backend initialization.')
                self._ranker.rerank('Backend initialization.', ['A fixed initialization passage.'])
            db=sqlite3.connect(self.index_path)
            db.execute('CREATE TABLE IF NOT EXISTS vectors(namespace TEXT,key TEXT,vector BLOB,PRIMARY KEY(namespace,key))')
            with self._lock:
                self.status='ready'
                if self._deferred is not None and not self._stop.is_set():
                    self._queue.put_nowait(self._deferred)
                    self._deferred=None
                    self._busy=True
                elif self._pending_primes and not self._stop.is_set():
                    self._queue.put_nowait(self._pending_primes.popitem(last=False)[1])
                    self._busy=True
                self.ready.set()
            while not self._stop.is_set():
                try:job=self._queue.get(timeout=.1)
                except queue.Empty:continue
                try:job['result']=self._score(db,job)
                except Exception as exc:job['result']={'status':'failed','reason':type(exc).__name__,'selected':{},'model':self.model,'policy':dict(self.policy),**({'diagnostic':exc.diagnostic} if isinstance(exc,LocalModelRequestError) else {})}
                finally:
                    with self._lock:
                        successor=self._deferred;self._deferred=None
                        if successor is not None and not self._stop.is_set():
                            if job.get('prime') and job.get('prime_paused'):
                                self._stage_prime(job)
                            self._queue.put_nowait(successor)
                        elif self._pending_primes and not self._stop.is_set():
                            self._queue.put_nowait(self._pending_primes.popitem(last=False)[1])
                        else:
                            self._busy=False
                            if successor is not None:self._finish_waiting(successor,'closed_before_start')
                    job['done'].set()
                    if 'vectors_ready' in job:job['vectors_ready'].set()
        except Exception as exc:
            with self._lock:
                self.status='failed';self.error=type(exc).__name__
                if self._deferred is not None:
                    job=self._deferred;self._deferred=None
                    job['result']=self._unavailable()
                    job['done'].set()
                    if 'vectors_ready' in job:job['vectors_ready'].set()
                self._pending_primes.clear()
                self.ready.set()
        finally:
            if db is not None:db.close()
            if self._engine is not None:self._engine.close()
            if self._ranker is not None and self._ranker is not self._engine:self._ranker.close()
    def _cached_documents(self, db, namespace, documents):
        ordered = iter(reversed(documents))
        while batch := tuple(islice(ordered, 128)):
            missing = tuple(dict.fromkeys(row['content_fingerprint'] for row in batch
                                          if (namespace, row['content_fingerprint']) not in self._vectors))
            stored = {}
            if missing:
                placeholders = ','.join('?' for _ in missing)
                stored = dict(db.execute(
                    f'SELECT key,vector FROM vectors WHERE namespace=? AND key IN ({placeholders})',
                    (namespace, *missing),
                ))
            for row in batch:
                yield row, stored.get(row['content_fingerprint'])

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
        layered=any('layer' in row for row in job['documents'])
        progressive=job.get('vectors_ready')
        cold_vector_deadline=min(vectors_started+self.vector_budget_s,started+self.work_budget_s) if layered else started+self.vector_budget_s
        scores={};hits=0;new=0
        # Reuse the entire cached slice before any cold encoding can consume
        # the vector window. The missing slice retains its original order.
        cached=[];missing=[]
        for row,saved in self._cached_documents(db,namespace,job['documents']):
            target=cached if saved is not None or (namespace,row['content_fingerprint']) in self._vectors else missing
            target.append((row,saved))
        prime=job.get('prime')
        background_index=prime and (job.get('incremental') or any('content_id' in row for row in job['documents']))
        document_count=len(job['documents'])
        if background_index:
            job['prime_paused']=False
        def publish_vectors():
            if progressive is None or not scores:return
            job['progress']={'status':'available','selected':{},'model':self.model,'provider':self.identity,
                'policy':dict(self.policy),'candidate_count':len(scores),'cached_vectors':hits,'new_vectors':new,
                'best_similarity':None,'coverage':{'total_documents':len(job['documents']),
                    'vector_examined':len(scores),'vector_unexamined':len(job['documents'])-len(scores),
                    'rank_examined':0,'rank_unexamined':len(scores),'unexamined_is_negative':False},
                'content_scores':{'method':'embedding','exchange_competition':True,'scores':dict(scores)},
                'timings_ms':{'query_embedding':query_ms,'document_vectors':round((time.perf_counter()-vectors_started)*1000),
                    'rerank':0,'total':round((time.perf_counter()-started)*1000)},'state_commit_authority':False}
            if len(scores)==len(job['documents']):progressive.set()
        last_publication=0.
        if background_index:
            hits=len(cached)
        cached_work=() if background_index else cached
        for index,(row,saved) in enumerate(chain(cached_work,missing)):
            if index==len(cached_work):publish_vectors()
            if not background_index and time.perf_counter()-vectors_started>=self.vector_budget_s:break
            if job.get('prime'):
                with self._lock:
                    if self._deferred is not None:
                        if background_index:job['prime_paused']=True
                        if background_index:
                            job['documents']=tuple(row for row,_ in missing[index:])
                        break
            if self._stop.is_set():raise RuntimeError('retrieval stopped')
            key=(namespace,row['content_fingerprint']);vector=self._vectors.get(key)
            if vector is None:
                if saved is not None:
                    vector=array('f');vector.frombytes(saved)
                    if len(vector)!=256 or any(not math.isfinite(x) for x in vector):raise ValueError('invalid saved vector')
                    hits+=1
                elif background_index or time.perf_counter()<cold_vector_deadline:
                    vector=self._engine.embed(row['text']);new+=1
                    db.execute('INSERT INTO vectors VALUES(?,?,?)',(*key,vector.tobytes()))
                    db.commit()
                else:continue
                self._vectors[key]=vector
            else:hits+=1
            if query is not None:scores[row['event_id']]=max(-1.,min(1.,sum(a*b for a,b in zip(query,vector))))
            if index>=len(cached_work) and time.perf_counter()-last_publication>=.025:
                publish_vectors()
                last_publication=time.perf_counter()
        publish_vectors()
        if job.get('prime'):return {'status':'indexed','new_vectors':new,'cached_vectors':hits,'unexamined_count':document_count-hits-new}
        vectors_ms=round((time.perf_counter()-vectors_started)*1000);rank_started=time.perf_counter()
        by_id={row['event_id']:row for row in job['documents']}
        def select(values, policy):
            if not layered:return select_scores(values,policy),{}
            groups={}
            for identity,value in values.items():
                groups.setdefault(by_id[identity]['layer'],{})[identity]=value
            # Interleave the qualified lists so an expensive layer cannot
            # consume the entire reranking budget before the others start.
            ranked=[select_scores(group,policy) for group in groups.values()]
            from itertools import zip_longest
            return ([key for row in zip_longest(*ranked) for key in row if key is not None],
                    {layer:max(group.values()) for layer,group in groups.items()})
        coarse,_=select(scores,EMBEDDING_POLICY)
        qualified=[by_id[i] for i in coarse]
        pending_layers={row['layer'] for row in qualified} if layered else set()
        job['qualified_layers']=pending_layers
        final={};rank_hits=0;rank_computed=0
        policy_key=hashlib.sha256(json.dumps(dict(self.policy),sort_keys=True,separators=(',',':')).encode()).hexdigest()
        def pair_key(row):return (*query_key,policy_key,row['content_fingerprint'],hashlib.sha256(row['text'].encode('utf8')).hexdigest())
        # Bounded computational caches, never a cap on candidate selection.
        for row in qualified:
            key=pair_key(row)
            if key in self._score_cache:
                final[row['event_id']]=self._score_cache[key];rank_hits+=1;self._score_cache.move_to_end(key)
        def publish():
            selected,layer_best=select(final,self.policy)
            snapshot={'status':'available','selected':{i:final[i] for i in selected},'model':self.model,'provider':self.identity,
                'policy':dict(self.policy),'candidate_count':len(scores),'cached_vectors':hits,'new_vectors':new,
                'best_similarity':max(final.values()) if final else None,'embedding_qualified_count':len(qualified),
                'coverage':{'total_documents':len(job['documents']),'vector_examined':len(scores),'rank_examined':len(final),
                    'rank_unexamined':len(qualified)-len(final),'vector_unexamined':len(job['documents'])-len(scores),'unexamined_is_negative':False},
                'scoring':'layered-embedding-qualified-reranker-v1' if layered else 'embedding-qualified-reranker-v1',
                **({'layer_best_similarity':layer_best} if layered else {}),
                'compute_reuse':{'version':'exact-source-query-pair-lru-v1','query_vector_hit':query_hit,'rerank_hits':rank_hits,'rerank_computed':rank_computed},
                'timings_ms':{'query_embedding':query_ms,'document_vectors':vectors_ms,'rerank':round((time.perf_counter()-rank_started)*1000),
                    'total':round((time.perf_counter()-started)*1000)},'state_commit_authority':False}
            if layered and scores:
                # Compare like scores. A partially completed rerank cannot
                # compete on a common scale with the remaining cosine scores.
                complete = len(final) == len(scores)
                snapshot['content_scores'] = {
                    'method': 'reranker' if complete else 'embedding',
                    'exchange_competition': True,
                    'scores': dict(final if complete else scores),
                    **({'partial_rerank':dict(final)} if final and not complete else {}),
                }
            # Each publication owns its dictionaries; never update a delivered prefix.
            if final or 'content_scores' in snapshot:job['progress']=snapshot
            return snapshot
        if final or (layered and scores):publish()
        # Publish the first fully reranked candidate before spending the
        # remaining compute budget on a batch. Candidate order is unchanged.
        pending=[row for row in qualified if row['event_id'] not in final]
        if layered:
            # Start with the best vector evidence for distinct original events.
            # Other layers remain eligible for the remaining compute budget.
            leaders={}
            for row in sorted(pending,key=lambda row:-scores[row['event_id']]):
                group=tuple(row.get('member_event_ids',(row['event_id'],)))
                leaders.setdefault(group,row['event_id'])
            first=set(leaders.values())
            pending=sorted(pending,key=lambda row:(
                row['event_id'] not in first,-scores[row['event_id']]))
        first_batch=3 if layered or final else 1
        batches=[pending[:first_batch],*(pending[i:i+3] for i in range(first_batch,len(pending),3))]
        for batch in batches:
            if time.perf_counter()-started>=self.work_budget_s:break
            if not batch:continue
            if progressive is not None:
                with self._lock:
                    if self._deferred is not None:break
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
        if any('content_id' in row for row in documents):job['vectors_ready']=threading.Event()
        immediate=None
        if self.status!='ready':immediate=self._unavailable()
        elif len({r['event_id'] for r in documents})!=len(documents):raise ValueError('duplicate local retrieval candidates')
        else:
            with self._lock:
                if self._busy or self._stop.is_set():immediate={'status':'busy','selected':{},'model':self.model,'policy':dict(self.policy)}
                else:self._busy=True;self._queue.put_nowait(job)
        if immediate is not None:job['result']=immediate;job['done'].set()
        return job

    def _unavailable(self):
        return {'status':self.status,'reason':self.error or self.status,
                'selected':{},'model':self.model,'policy':dict(self.policy)}

    def _finish_waiting(self,job,reason):
        job['result']={'status':'busy','reason':reason,'selected':{},'model':self.model,'policy':dict(self.policy)}
        job['done'].set()
        if 'vectors_ready' in job:job['vectors_ready'].set()

    def submit_latest(self,*,query,documents,namespace):
        """One replaceable waiting query; existing delivered snapshots stay immutable."""
        if len({r['event_id'] for r in documents})!=len(documents):raise ValueError('duplicate local retrieval candidates')
        job={'query':query,'documents':documents,'namespace':namespace,'done':threading.Event()}
        if any('content_id' in row for row in documents):job['vectors_ready']=threading.Event()
        with self._lock:
            if self._stop.is_set():self._finish_waiting(job,'closed_before_start')
            elif self.status not in ('warming','ready'):
                job['result']=self._unavailable();job['done'].set()
            elif self.status=='warming' or self._busy:
                if self._deferred is not None:self._finish_waiting(self._deferred,'superseded_before_start')
                self._deferred=job
            else:self._busy=True;self._queue.put_nowait(job)
        return job

    def result(self,job,*,timeout_s=.5):
        if not job['done'].is_set():
            progress=job.get('progress')
            vector_ready='vectors_ready' in job and job['vectors_ready'].is_set()
            ranked_ready=progress and progress['selected'] and (
                job.get('qualified_layers',set())<=progress.get('layer_best_similarity',{}).keys())
            if progress is not None and (vector_ready or ranked_ready):
                return {**deepcopy(progress),'publication':'foreground-ready-snapshot-v1'}
        ready=job.get('vectors_ready',job['done'])
        ready.wait(timeout_s)
        progress=job.get('progress')
        if not job['done'].is_set():
            if progress is not None:
                publication=('foreground-ready-snapshot-v1' if ready is not job['done'] and ready.is_set()
                             else 'foreground-deadline-snapshot-v1')
                return {**deepcopy(progress),'publication':publication}
            return {'status':'timeout','selected':{},'model':self.model,'policy':dict(self.policy)}
        if job['result']['status']=='failed' and ready is not job['done'] and progress is not None:
            return {**deepcopy(progress),'publication':'foreground-partial-after-failure-v1'}
        return job['result']
    def retrieve(self,*,query,documents,namespace,timeout_s=.5):
        return self.result(self.submit(query=query,documents=documents,namespace=namespace),timeout_s=timeout_s)
    def _stage_prime(self,job):
        namespace=job['namespace']
        previous=self._pending_primes.get(namespace)
        if previous is not None:
            documents={row['event_id']:row for row in (*previous['documents'],*job['documents'])}
            job={**job,'documents':tuple(documents.values())}
        self._pending_primes[namespace]=job

    def prime(self,*,documents,namespace,incremental=False):
        job={'documents':documents,'namespace':namespace,'prime':True,'incremental':incremental,'done':threading.Event()}
        with self._lock:
            if self.status=='warming' and not self._stop.is_set():
                self._stage_prime(job)
                return True
            if self.status!='ready':return False
            if self._stop.is_set():return False
            if self._busy:
                if incremental or any('content_id' in row for row in documents):
                    self._stage_prime(job)
                    return True
                return False
            self._busy=True;self._queue.put_nowait(job)
        return True
    def close(self):
        self._stop.set()
        with self._lock:
            self._pending_primes.clear()
            if self._deferred is not None:self._finish_waiting(self._deferred,'closed_before_start');self._deferred=None
            try:queued=self._queue.get_nowait()
            except queue.Empty:pass
            else:self._finish_waiting(queued,'closed_before_start')
        if self._engine is not None:self._engine.close()
        if self._ranker is not None and self._ranker is not self._engine:self._ranker.close()
        if self._thread is not None:self._thread.join(3)
        if self._thread is not None and self._thread.is_alive():raise RuntimeError('local retrieval worker did not close')
