"""On-demand operational snapshots. No provider, runtime or replay authority."""
from pathlib import Path
import json
import math
import queue
import threading


def _difference(end,start):
    if type(end) is int and type(start) is int and end>=start:return end-start
    return None


def _number(value):
    return value if type(value) in (int,float) and math.isfinite(value) and value>=0 else None


def read_snapshot(trace_dir,*,secrets=(),playback_dir=None):
    directory=Path(trace_dir)
    result={'providers':[],'writer':None,'host_job':None,'unreadable_files':0,'recent_limit':40,'response_timing':None}
    def redact(value):
        if isinstance(value,str):
            for secret in secrets:
                if secret:value=value.replace(secret,'[已隐藏配置密钥]')
            return value
        if isinstance(value,list):return [redact(item) for item in value]
        if isinstance(value,dict):return {key:redact(item) for key,item in value.items()}
        return value
    try:
        paths=sorted(directory.glob('*.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True)[:40]
    except OSError:paths=[];result['unreadable_files']+=1
    records=[]
    for path in paths:
        try:
            if path.stat().st_size>16*1024*1024:raise ValueError('oversize trace')
            row=json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(row,dict):raise ValueError('invalid trace')
        except (OSError,ValueError):result['unreadable_files']+=1;continue
        if row.get('version')!='petlab-provider-trace-v1':continue
        timing=row.get('host_timing') or {};processing=row.get('host_processing') or {}
        start=timing.get('started_at_unix_ms');end=timing.get('transport_finished_at_unix_ms')
        item={'file':path.name,'invocation_id':row.get('invocation_id'),'operation_kind':row.get('operation_kind'),
            'status':row.get('status'),'started_at_unix_ms':start,'network_ms':_difference(end,start),
            'postprocess_ms':_difference(processing.get('completed_at_unix_ms'),end)}
        if row.get('status')=='failed':
            failure=(row.get('execution') or {}).get('failure') or row.get('codec_parse') or {}
            kind=failure.get('failure_kind') if isinstance(failure,dict) else None
            if isinstance(kind,str):item['failure_kind']=redact(kind[:160])
        records.append((item,row))
    records.sort(key=lambda pair:pair[0]['started_at_unix_ms'] if type(pair[0]['started_at_unix_ms']) is int else -1,reverse=True)
    result['providers']=[pair[0] for pair in records]
    for item,row in records:
        if item['operation_kind']=='writer':
            messages=(row.get('request_payload') or {}).get('messages')
            if isinstance(messages,list):result['writer']={'file':item['file'],'invocation_id':item['invocation_id'],'status':item['status'],'messages':redact(messages)}
            break
    journal=directory/'host-jobs.jsonl'
    if journal.exists():
        try:
            # Read only the tail; a partially appended final line is not a completed job.
            with journal.open('rb') as stream:
                stream.seek(0,2);size=stream.tell();offset=max(0,size-131072);stream.seek(offset);data=stream.read()
            lines=data.decode('utf-8',errors='replace').splitlines()
            if offset:lines=lines[1:]
            for line in reversed(lines):
                try:row=json.loads(line)
                except ValueError:continue
                if not isinstance(row,dict) or row.get('version')!='petlab-host-job-timing-v1':continue
                result['host_job']={key:row.get(key) for key in ('job_sequence','kind','status','received_at_unix_ms','started_at_unix_ms','finished_at_unix_ms')}
                result['host_job'].update(queue_ms=_difference(row.get('started_at_unix_ms'),row.get('received_at_unix_ms')),processing_ms=_number(row.get('processing_ms')))
                error=row.get('error')
                if isinstance(error,dict):
                    result['host_job']['error']={'type':redact(str(error.get('type',''))[:128]),
                        'frames':[{'file':Path(str(f.get('file',''))).name[:120],
                            'line':f.get('line') if type(f.get('line')) is int else None,
                            'function':redact(str(f.get('function',''))[:128])}
                            for f in error.get('frames',())[-4:] if isinstance(f,dict)]}
                from .response_timing import load_response_timing
                result['response_timing']=load_response_timing(directory,playback_dir,row)
                break
        except OSError:result['unreadable_files']+=1
    return result


def render_snapshot(snapshot,*,capabilities=None):
    def duration(value):return '未知/尚未完成' if value is None else f'{value:.1f} ms'
    lines=['本地只读诊断 · 手动刷新','以下为分别记录的后台任务与请求；不自动归并为同一次交互，也不是点击到开口总耗时。','']
    job=snapshot['host_job']
    if job:
        lines+=['最近已记录后台任务',f"#{job['job_sequence']} {job['kind']} · {job['status']}",
            '后台排队：'+duration(job['queue_ms'])+'；任务处理：'+duration(job['processing_ms']),
            '任务处理包含各阶段及可能的界面执行等待，不等于用户等待时长。','']
        error=job.get('error')
        if error:
            lines+=['本地错误类型：'+error['type']]
            lines.extend(f"  {f['file']}:{f['line']} · {f['function']}" for f in error['frames'])
            lines+=['']
    else:lines+=['暂无已记录后台任务。','']
    chain=snapshot.get('response_timing')
    if chain and chain['responses']:
        lines+=['按原输入与实际动作回执关联的响应']
        for response in chain['responses']:
            lines+=[str(response['source_fact_ref'])+' → '+response['action_id'],
                '接触起点到文字绘制提交：'+duration(response['from_contact_ms']),
                '本段结束到文字绘制提交：'+duration(response['after_interval_ms']),
                '区间结束到封段：'+duration(response['capture_to_seal_ms'])+'；封段到后台收到：'+duration(response['seal_to_host_receive_ms'])]
        lines+=['绘制提交是Tk完成文字布局/绘制后的记录，不等于屏幕硬件扫描完成；跨恢复时包含等待恢复的时间。','']
    if chain and chain['providers']:
        lines+=['当前后台作业已关联API（精确调用编号）']
        for call in chain['providers']:
            lines+=[str(call['operation_kind'])+' · '+str(call['status'])+' · '+call['invocation_id'],
                '来源：'+str(call['source_fact_ref'])+'；网络：'+duration(call['network_ms'])]
        lines+=['']
    lines+=['近期实际提供方请求（最多40个最近修改的JSON文件）']
    labels={'writer':'表达 Writer','semantic_measurement':'语义量测'}
    for row in snapshot['providers']:
        lines+=[f"{labels.get(row['operation_kind'],row['operation_kind'])} · {row['status']} · {row['file']}",
            '网络：'+duration(row['network_ms'])+'；网络返回后处理：'+duration(row['postprocess_ms'])]
        if row.get('failure_kind'):lines+=['失败码：'+row['failure_kind']]
    if not snapshot['providers']:lines+=['暂无提供方请求。']
    if snapshot['unreadable_files']:lines+=[f"有{snapshot['unreadable_files']}个记录暂不可读或过大；可稍后手动刷新。"]
    lines+=['','当前已加载形象能力']
    if capabilities:
        for code,label in capabilities['choices'].items():lines+=[code+' / '+label+'：'+capabilities['descriptions'][code]]
    else:lines+=['未发布形象能力声明，沿用旧表达合同。']
    writer=snapshot['writer'];lines+=['','最近实际Writer请求正文（来自提供方记录，配置密钥已隐藏）']
    if writer:
        lines+=[writer['file']+' · '+str(writer['status']), '这是已保存的请求正文；started/prepared不证明服务已收到。',json.dumps(writer['messages'],ensure_ascii=False,indent=2)]
    else:lines+=['暂无实际Writer请求；不以当前配置重建。']
    return '\n'.join(lines)


class DiagnosticsWindow:
    def __init__(self,root,*,trace_dir,capabilities=None,secrets=(),playback_dir=None):
        import tkinter as tk
        from tkinter.scrolledtext import ScrolledText
        self.playback_dir=playback_dir
        self.root=root;self.trace_dir=trace_dir;self.capabilities=capabilities;self.secrets=tuple(s for s in secrets if s)
        self.closed=False;self.loading=False;self._alarm=None;self._results=queue.Queue()
        self._content="";self._page=0;self.page_chars=32000
        self.window=tk.Toplevel(root);self.window.title('TIDE 桌宠 · 本地诊断');self.window.geometry('780x620')
        self.window.protocol('WM_DELETE_WINDOW',self.close)
        toolbar=tk.Frame(self.window);toolbar.pack(fill='x',padx=10,pady=8)
        self.refresh_button=tk.Button(toolbar,text='刷新本地记录',command=self.refresh);self.refresh_button.pack(side='left')
        self.back=tk.Button(toolbar,text='上一页',command=self.previous_page);self.back.pack(side='left',padx=(12,2))
        self.forward=tk.Button(toolbar,text='下一页',command=self.next_page);self.forward.pack(side='left')
        self.page_label=tk.Label(toolbar,text='读取中…');self.page_label.pack(side='left',padx=10)
        self.text=ScrolledText(self.window,wrap='word',font=('Microsoft YaHei UI',10));self.text.pack(fill='both',expand=True,padx=10,pady=(0,10))
        self.refresh()

    def refresh(self):
        if self.closed or self.loading:return
        self.loading=True;self.refresh_button.configure(state='disabled')
        def read():
            try:value=render_snapshot(read_snapshot(self.trace_dir,secrets=self.secrets,playback_dir=self.playback_dir),capabilities=self.capabilities)
            except Exception:value='本地诊断记录暂不可读。请稍后刷新。'
            for secret in self.secrets:value=value.replace(secret,'[已隐藏配置密钥]')
            self._results.put(value)
        threading.Thread(target=read,daemon=True,name='pet-diagnostics-reader').start()
        self._alarm=self.root.after(30,self._poll)

    def _poll(self):
        self._alarm=None
        if self.closed:return
        try:value=self._results.get_nowait()
        except queue.Empty:self._alarm=self.root.after(30,self._poll);return
        self._content=value;self._page=0;self._show_page()
        self.loading=False;self.refresh_button.configure(state='normal')

    def _show_page(self):
        count=max(1,(len(self._content)+self.page_chars-1)//self.page_chars)
        start=self._page*self.page_chars
        self.text.configure(state='normal');self.text.delete('1.0','end')
        self.text.insert('1.0',self._content[start:start+self.page_chars]);self.text.configure(state='disabled')
        self.page_label.configure(text=f'{self._page+1} / {count} 页 · 完整正文可翻页查看')
        self.back.configure(state='normal' if self._page else 'disabled')
        self.forward.configure(state='normal' if self._page+1<count else 'disabled')

    def next_page(self):
        if not self.closed and (self._page+1)*self.page_chars<len(self._content):self._page+=1;self._show_page()

    def previous_page(self):
        if not self.closed and self._page:self._page-=1;self._show_page()

    def close(self):
        if self.closed:return
        self.closed=True
        if self._alarm is not None:self.root.after_cancel(self._alarm);self._alarm=None
        self.window.destroy()
