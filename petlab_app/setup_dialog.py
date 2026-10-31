"""Explicit, pre-launch role/provider configuration; no psychological UI logic."""
from __future__ import annotations

from dataclasses import dataclass,replace
from copy import deepcopy
import json,os,tempfile,uuid,queue,threading,time
from pathlib import Path
import tkinter as tk
from tkinter import ttk,filedialog

from framework.model_api import model_data_fingerprint
from framework.runtime_checkpoint import read_runtime_checkpoint
from .composition import DEFINITION_KEY,PetDefinition
from .desktop_app import configuration_from_record
from .storage import RUNTIME_IDENTITY,_disk_decode

BASE=Path(__file__).resolve().parents[1]
CONSTANTS=("a1","a2","b1","b2","b3","g1","g2")
THINKING_OPTIONS={"沿用提供方默认":None,"关闭思考":"disabled","开启思考":"enabled"}


def read_configuration_record(path):
    value=json.loads(Path(path).read_text(encoding="utf-8-sig"),
        parse_constant=lambda value:(_ for _ in ()).throw(ValueError("non-finite configuration")))
    configuration_from_record(value,require_api_key=False)
    return value


def _atomic_json(path,value):
    encoded=json.dumps(value,ensure_ascii=False,allow_nan=False,indent=2)+"\n"
    path=Path(path).resolve();path.parent.mkdir(parents=True,exist_ok=True)
    descriptor,temporary=tempfile.mkstemp(prefix=path.name+".",suffix=".tmp",dir=path.parent)
    try:
        with os.fdopen(descriptor,"w",encoding="utf-8",newline="\n") as output:
            output.write(encoded);output.flush();os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def save_configuration(path,value):
    configuration_from_record(value,require_api_key=False)
    _atomic_json(path,value)


def check_session_definition(definition,path):
    """Read only the original checkpoint's identity/definition, never derive UI behavior from state."""
    path=Path(path)
    if not path.exists():return
    if path.is_dir():raise ValueError("session file is a directory")
    value=_disk_decode(json.loads(path.read_text(encoding="utf-8")))
    checkpoint=read_runtime_checkpoint(value,expected_runtime_identity=RUNTIME_IDENTITY,
        expected_session_id=definition.session_id)
    selected=checkpoint["profile"].get(DEFINITION_KEY)
    if selected is None or model_data_fingerprint(selected)!=definition.fingerprint:
        raise ValueError("saved session definition differs; select a new session file for a changed role")


@dataclass(frozen=True)
class LaunchSelection:
    configuration:object
    config_path:Path
    session_path:Path
    trace_dir:Path
    asset_dir:Path|None=None
    multi_workspace:Path|None=None

    def path_record(self):
        value={"version":"desktop-pet-launcher-v1","config_path":str(self.config_path),
            "session_path":str(self.session_path),"trace_dir":str(self.trace_dir)}
        if self.asset_dir is not None:value.update(version="desktop-pet-launcher-v2",asset_dir=str(self.asset_dir))
        if self.multi_workspace is not None:value.update(version="desktop-pet-launcher-v3",multi_workspace=str(self.multi_workspace))
        return value


def read_launch_paths(path):
    path=Path(path)
    if not path.exists():return None
    value=json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value,dict):raise ValueError("launcher path record is invalid")
    keys=("config_path","session_path","trace_dir")
    if value.get('version')=='desktop-pet-launcher-v3':keys=keys+('multi_workspace',)+(('asset_dir',) if 'asset_dir' in value else ())
    elif value.get('version')=='desktop-pet-launcher-v2':keys=keys+('asset_dir',)
    elif value.get('version')!='desktop-pet-launcher-v1':raise ValueError("launcher path record is invalid")
    if set(value)!={'version',*keys}:raise ValueError("launcher path record is invalid")
    result={}
    for key in keys:
        if not isinstance(value[key],str) or not value[key].strip() or not Path(value[key]).is_absolute():
            raise ValueError("launcher path must be absolute")
        result[key]=Path(value[key])
    if result["config_path"]==result["session_path"]:raise ValueError("configuration and memory files must differ")
    return result


class PetSetupDialog:
    def __init__(self,root,*,config_path,session_path,trace_dir,preferences_path=None,asset_dir=None,multi_workspace=None,creating=False):
        self.root=root;self.result=None;self.last_error=None
        self.creating=creating;self._created_definition=None
        self._creation_pending=False
        self.preferences_path=None if preferences_path is None else Path(preferences_path)
        self.multi_workspace=None if multi_workspace is None else Path(multi_workspace)
        self._seed_unavailable=False
        try:
            self.seed=(read_configuration_record(config_path) if Path(config_path).exists() else
                read_configuration_record(BASE/"config.example.json"))
            if not Path(config_path).exists():self._fresh_identity(self.seed)
        except (ValueError,OSError):
            if not Path(config_path).exists():raise
            # This template only makes recovery controls usable. It is never
            # accepted as a replacement for the unreadable role or its history.
            self.seed=read_configuration_record(BASE/"config.example.json")
            self._seed_unavailable=True
        if creating:
            provider=deepcopy(self.seed['provider'])
            self.seed=read_configuration_record(BASE/'config.example.json')
            self.seed['provider']=provider
            self.seed.pop('expression_profile',None)
            self._fresh_identity(self.seed)
            self.seed['definition'].update(name='',background='')
            self._seed_unavailable=False;self.multi_workspace=None
            folder=Path(config_path).resolve().parent/'characters'/uuid.uuid4().hex
            config_path=folder/'config.json';session_path=folder/'session.json';trace_dir=folder/'traces'
        self.window=tk.Toplevel(root);self.window.title("TIDE · 创建自己的角色" if creating else "TIDE · 桌宠设置")
        self.window.resizable(False,False)
        self.fields={}
        names=self.seed["definition"]["name"]
        if self.multi_workspace is not None:
            from .multi_pet import DEFAULT_NAMES
            names='、'.join(DEFAULT_NAMES.values())
            manifest=self.multi_workspace/'workspace.json'
            if manifest.exists():
                try:names='、'.join(v['name'] for v in json.loads(manifest.read_text('utf8'))['definitions'].values())
                except (OSError,ValueError,KeyError,TypeError):pass # actual identity validation still blocks start
        ttk.Label(self.window,text=("创建一个独立角色：先填名称与人设，再配置API并按背景生成。外观暂用现有鲸鱼动画。" if creating else "启动角色："+names+"；在下方“API 密钥”框粘贴自己的密钥后，点击“保存并启动”。"),wraplength=540).pack(fill="x",padx=14,pady=(12,0))
        notebook=ttk.Notebook(self.window);notebook.pack(fill="both",expand=True,padx=14,pady=12)
        role=ttk.Frame(notebook,padding=12);provider=ttk.Frame(notebook,padding=12);files=ttk.Frame(notebook,padding=12)
        self.notebook=notebook
        if creating:notebook.add(role,text='新角色')
        notebook.add(provider,text="模型");notebook.add(files,text="保存位置")
        self._entry(role,"name","名称",0)
        ttk.Label(role,text="角色背景").grid(row=1,column=0,sticky="nw",pady=6)
        self.background=tk.Text(role,width=46,height=5,wrap="word",font=("Microsoft YaHei UI",10))
        self.background.grid(row=1,column=1,sticky="ew",pady=6)
        self.advanced=ttk.Frame(role)
        ttk.Button(role,text="角色参数…",command=self.toggle_parameters).grid(row=2,column=1,sticky="w",pady=6)
        self.measure_button=ttk.Button(role,text="按背景生成",command=self.measure_background)
        self.measure_button.grid(row=2,column=0,sticky="w",pady=6)
        ttk.Label(role,text="填写名称和完整人设，在“模型”页填API，再按背景生成；成功后保存并启动。首版复用现有鲸鱼动画。" if creating else "新角色可显式生成一次人格卡与参数；仅保存不会请求模型。",wraplength=380).grid(row=3,column=0,columnspan=2,sticky="w")
        for row,key in enumerate(CONSTANTS):self._entry(self.advanced,key,key,row)
        for row,(key,label) in enumerate([("endpoint","接口地址"),("model","模型名称"),("api_key_env","密钥变量名（通常不用改）")]):
            self._entry(provider,key,label,row)
        self._entry(provider,"writer_model","表达模型（留空沿用上方）",8)
        self.secret=tk.StringVar(master=self.window)
        ttk.Label(provider,text="API 密钥（填在这里）").grid(row=3,column=0,sticky="w",padx=(0,10),pady=6)
        ttk.Entry(provider,textvariable=self.secret,show="•",width=42).grid(row=3,column=1,sticky="ew",pady=6)
        self.remember_secret=tk.BooleanVar(master=self.window,value=False)
        ttk.Checkbutton(provider,text="在本机安全保存密钥（仅当前 Windows 用户可读取）",variable=self.remember_secret).grid(row=4,column=0,columnspan=2,sticky="w",pady=6)
        self._entry(provider,"writer_temperature","表达温度",5)
        self.thinking=tk.StringVar(master=self.window)
        ttk.Label(provider,text="思考模式").grid(row=6,column=0,sticky="w",pady=6)
        ttk.Combobox(provider,textvariable=self.thinking,values=list(THINKING_OPTIONS),state="readonly",width=39).grid(row=6,column=1,sticky="ew",pady=6)
        ttk.Label(provider,text="DeepSeek日常交互建议关闭；其他接口需支持此参数。沿用默认可能开启思考。",wraplength=380).grid(row=7,column=0,columnspan=2,sticky="w",pady=6)
        file_rows=[("config_path","配置文件",config_path),("session_path","记忆文件",session_path),("trace_dir","运行记录",trace_dir),("asset_dir","素材目录",asset_dir or BASE/"assets/pets/deepseek-maid-v1")]
        if self.multi_workspace is not None:
            for key,value in (('session_path',session_path),('trace_dir',trace_dir)):
                self.fields[key]=tk.StringVar(master=self.window,value=str(Path(value).resolve()))
            file_rows=[file_rows[0],('multi_workspace','两只角色的存档目录',self.multi_workspace),file_rows[-1]]
        for row,(key,label,value) in enumerate(file_rows):
            self._entry(files,key,label,row,width=44);self.fields[key].set(str(Path(value).resolve()))
            ttk.Button(files,text="选择…",command=lambda key=key:self.choose_path(key)).grid(row=row,column=2,padx=5)
        ttk.Label(files,text="两只角色各自保存记忆；恢复时选择原存档目录。" if self.multi_workspace is not None else "默认保存本机小鲸鱼配置和记忆；恢复已有角色时选择其原文件。",wraplength=420).grid(row=4,column=0,columnspan=3,sticky="w",pady=12)
        self.observe_during_hold=tk.BooleanVar(master=self.window)
        ttk.Checkbutton(files,text="启用持续交互",variable=self.observe_during_hold).grid(row=5,column=0,columnspan=3,sticky="w",pady=6)
        ttk.Label(files,text="关闭后按操作结束处理；本模式的主动搭话也会关闭。",wraplength=420).grid(row=6,column=0,columnspan=3,sticky="w")
        ttk.Label(files,text="角色会按当前状态选择是否活动：可以只讲话，也可以边玩边聊。",wraplength=420).grid(row=7,column=0,columnspan=3,sticky="w",pady=6)
        self.check_button=ttk.Button(files,text="本地检查（不连接模型）",command=self.check_local)
        self.check_button.grid(row=8,column=0,columnspan=3,sticky="w",pady=6)
        toolbar=ttk.Frame(self.window);toolbar.pack(fill="x",padx=14,pady=(0,8))
        if not creating:
            self.create_button=ttk.Button(toolbar,text='创建自己的角色…',command=self.create_character)
            self.create_button.pack(side='left',padx=(0,8))
        ttk.Button(toolbar,text="加载配置…",command=self.import_configuration).pack(side="left")
        ttk.Button(toolbar,text="取消",command=self.cancel).pack(side="right")
        self.start_button=ttk.Button(toolbar,text="保存并启动",command=self.start);self.start_button.pack(side="right",padx=8)
        self.save_button=ttk.Button(toolbar,text="仅保存配置",command=self.save);self.save_button.pack(side="right")
        self.status=tk.StringVar(master=self.window)
        ttk.Label(self.window,textvariable=self.status,wraplength=540).pack(fill="x",padx=14,pady=(0,12))
        self.window.protocol("WM_DELETE_WINDOW",self.cancel)
        self.window.bind("<Escape>",lambda event:self.cancel())
        self.populate()
        if self._seed_unavailable:
            self.status.set("原配置暂不可读取，文件和记忆已保留。请加载有效的原配置。")

    @staticmethod
    def _fresh_identity(record):
        token=uuid.uuid4().hex
        record["definition"].update(session_id="petlab:"+token,owner_ref="pet:"+token,scope_ref="scope:desktop:"+token)

    def _entry(self,parent,key,label,row,width=42):
        variable=tk.StringVar(master=self.window);self.fields[key]=variable
        ttk.Label(parent,text=label).grid(row=row,column=0,sticky="w",padx=(0,10),pady=6)
        ttk.Entry(parent,textvariable=variable,width=width).grid(row=row,column=1,sticky="ew",pady=6)

    def populate(self):
        for key in ("name",):self.fields[key].set(self.seed["definition"][key])
        self.background.delete("1.0","end");self.background.insert("1.0",self.seed["definition"]["background"])
        for key in CONSTANTS:self.fields[key].set(str(self.seed["definition"]["base_constants"][key]))
        for key in ("endpoint","model","api_key_env"):self.fields[key].set(self.seed["provider"].get(key,""))
        self.fields["writer_model"].set(self.seed.get("writer_model") or "")
        self.fields["writer_temperature"].set(str(self.seed["writer_temperature"]))
        self.thinking.set(next(key for key,value in THINKING_OPTIONS.items() if value==self.seed["provider"].get("thinking_mode")))
        self.observe_during_hold.set(self.seed["mouse"].get("observation",{}).get("enabled",False))
        self.secret.set("")

    def measure_background(self):
        if self._creation_pending:return
        try:
            from .composition import background_creation_context,definition_from_background
            from .providers import ProviderGateway
            from product_adapters.desktop_pet_v1.creation import measure_background
            record=self.record();paths=self._paths()
            if paths["session_path"].exists():raise ValueError("已有角色定义固定；请先新建角色或选择新的记忆文件。")
            secret=self.secret.get()
            selected=configuration_from_record(record,require_api_key=not bool(secret))
            transport=replace(selected.transport,api_key=secret) if secret else selected.transport
            snapshot=model_data_fingerprint(record);path_snapshot={k:str(v) for k,v in paths.items()}
            binding,position=background_creation_context(session_id=selected.definition.session_id,coordinate=time.time_ns()//1_000_000)
            answers=queue.Queue();self._creation_pending=True;self.last_error=None
            for button in (self.measure_button,self.start_button,self.save_button):button.state(["disabled"])
            self.status.set("正在解析背景；结果只更新新角色草稿，不创建记忆…")
            def work():
                try:
                    measured=measure_background(description="角色名称："+selected.definition.name+"\n"+selected.definition.background,
                        session_binding=binding,current_position=position,
                        gateway=ProviderGateway(transport,trace_dir=paths["trace_dir"]/"creation"))
                    answers.put((measured,None))
                except Exception as error:answers.put((None,str(error)))
            def collect():
                try:
                    if not self.window.winfo_exists():return
                except tk.TclError:return
                try:measured,error=answers.get_nowait()
                except queue.Empty:self.window.after(50,collect);return
                self._creation_pending=False
                for button in (self.measure_button,self.start_button,self.save_button):button.state(["!disabled"])
                try:
                    if error:raise RuntimeError(error)
                    if (model_data_fingerprint(self.record())!=snapshot or self.secret.get()!=secret
                            or {k:str(v) for k,v in self._paths().items()}!=path_snapshot):
                        raise ValueError("设置已经改动，生成结果没有覆盖草稿；请按新描述重新生成。")
                    definition=definition_from_background(selected.definition,measured)
                    if self.creating:
                        # The generated card and original interpretation stay in
                        # the creation receipt; expression keeps the full author text.
                        definition=replace(definition,background=record['definition']['background'])
                    _atomic_json(paths["trace_dir"]/"creation"/("profile-"+measured["receipt"]["fingerprint"]+".json"),measured)
                    self.seed=deepcopy(record);self.seed["definition"]=definition.to_record();self.populate();self.secret.set(secret)
                    if self.creating:self._created_definition=definition.fingerprint
                    self.status.set("人格卡与参数已生成。可核对后保存；未产生互动或记忆。")
                except Exception as error:self._error(error)
            threading.Thread(target=work,daemon=True).start();self.window.after(50,collect)
        except Exception as error:self._error(error)

    def toggle_parameters(self):
        if self.advanced.winfo_manager():self.advanced.grid_remove()
        else:self.advanced.grid(row=4,column=0,columnspan=2,sticky="ew",pady=8)

    def record(self):
        if self._seed_unavailable:
            raise ValueError("原配置暂不可读取；请先加载有效的原配置。")
        value=deepcopy(self.seed);definition=value["definition"]
        writer_model=self.fields["writer_model"].get().strip()
        if writer_model:value["writer_model"]=writer_model
        else:value.pop("writer_model",None)
        definition["name"]=self.fields["name"].get().strip()
        definition["background"]=self.background.get("1.0","end-1c")
        constants={key:float(self.fields[key].get()) for key in CONSTANTS}
        if constants!=definition["base_constants"]:
            definition["base_constants_version"]="petlab-ui-constants:"+model_data_fingerprint(constants)
        definition["base_constants"]=constants
        for key in ("endpoint","model"):value["provider"][key]=self.fields[key].get().strip()
        key_env=self.fields["api_key_env"].get().strip()
        if key_env:value["provider"]["api_key_env"]=key_env
        else:value["provider"].pop("api_key_env",None)
        if self.observe_during_hold.get() or "observation" in value["mouse"]:
            value["mouse"]["observation"]={"enabled":self.observe_during_hold.get(),"bounds_ms":value["mouse"].get("observation",{}).get("bounds_ms",[2000,5000])}
        if self.observe_during_hold.get() and value.get('opening_mode')=='shared-opening-v1':
            value['idle_behavior']='symbolic'
        if not self.observe_during_hold.get():
            value['mouse'].pop('batch_quiet_ms',None)
            if value.get('opening_mode')=='shared-opening-v1':value['opening_mode']='legacy'
            if 'idle_behavior' in value:value['idle_behavior']='speech'
        thinking=self.thinking.get()
        if thinking not in THINKING_OPTIONS:raise ValueError("thinking mode selection is invalid")
        if THINKING_OPTIONS[thinking] is None:value["provider"].pop("thinking_mode",None)
        else:value["provider"]["thinking_mode"]=THINKING_OPTIONS[thinking]
        value["writer_temperature"]=float(self.fields["writer_temperature"].get())
        configuration_from_record(value,require_api_key=False)
        return value

    def choose_path(self,key):
        initial=Path(self.fields[key].get()).parent
        selected=(filedialog.askdirectory(parent=self.window,initialdir=initial) if key in {"trace_dir","asset_dir","multi_workspace"} else
            filedialog.asksaveasfilename(parent=self.window,initialdir=initial,defaultextension=".json",filetypes=[("JSON","*.json")],confirmoverwrite=False))
        if selected:self.fields[key].set(selected)

    def import_configuration(self):
        selected=filedialog.askopenfilename(parent=self.window,initialdir=BASE,filetypes=[("JSON","*.json")])
        if not selected:return
        try:
            seed=read_configuration_record(selected)
            self.seed=seed;self._seed_unavailable=False;self.fields["config_path"].set(str(Path(selected).resolve()));self.populate()
            self.status.set("已加载配置；请核对对应的记忆文件。")
        except Exception as error:self._error(error)

    def new_role(self):
        self.seed=read_configuration_record(BASE/"config.example.json");self._fresh_identity(self.seed)
        self._seed_unavailable=False
        token=self.seed["definition"]["session_id"].split(":")[-1]
        folder=BASE/"output"/"pets"/token
        for key,name in [("config_path","config.json"),("session_path","session.json"),("trace_dir","traces")]:self.fields[key].set(str(folder/name))
        self.populate();self.status.set("新角色使用新的记忆文件。")

    def create_character(self):
        if self._creation_pending:return
        try:
            paths=self._paths();secret=self.secret.get();current=self.record()
            dialog=PetSetupDialog(self.root,config_path=paths['config_path'],
                session_path=paths['session_path'],trace_dir=paths['trace_dir'],
                preferences_path=self.preferences_path,asset_dir=paths['asset_dir'],creating=True)
            # Carry edited provider fields into the draft, never the old persona.
            dialog.seed['provider']=deepcopy(current['provider'])
            dialog.seed['writer_temperature']=current['writer_temperature']
            if current.get('writer_model'):dialog.seed['writer_model']=current['writer_model']
            dialog.populate();dialog.secret.set(secret)
            self.window.withdraw();self.root.wait_window(dialog.window)
            if dialog.result is not None:
                self.result=dialog.result;self.secret.set('');self.window.destroy()
            else:self.window.deiconify()
        except Exception as error:
            self.window.deiconify();self._error(error)

    def _require_created(self,value):
        if self.creating and PetDefinition.from_record(value['definition']).fingerprint!=self._created_definition:
            raise ValueError('请先按背景生成角色参数；名称、人设或参数改动后需重新生成。')

    def _paths(self):
        result={key:Path(self.fields[key].get()).resolve() for key in ("config_path","session_path","trace_dir","asset_dir")}
        if self.multi_workspace is not None:result['multi_workspace']=Path(self.fields['multi_workspace'].get()).resolve()
        if not all(self.fields[key].get().strip() for key in result):raise ValueError("save paths are required")
        if result["config_path"]==result["session_path"]:raise ValueError("configuration and memory files must differ")
        if any(result[key].is_dir() for key in ("config_path","session_path")):raise ValueError("configuration and memory paths must be files")
        if result["trace_dir"] in (result["config_path"],result["session_path"]):raise ValueError("trace directory must differ from save files")
        if result["trace_dir"].exists() and not result["trace_dir"].is_dir():raise ValueError("trace path must be a directory")
        if self.preferences_path is not None and self.preferences_path.resolve() in result.values():raise ValueError("launcher preferences must have a separate path")
        if not result['asset_dir'].is_dir():raise ValueError('素材目录不存在')
        if not (result['asset_dir']/'manifest.json').is_file():raise ValueError('素材目录缺少manifest.json')
        if result['asset_dir']==result['trace_dir']:raise ValueError('素材目录和运行记录目录应分开')
        if 'multi_workspace' in result:
            folder=result['multi_workspace']
            if folder.exists() and not folder.is_dir():raise ValueError('双宠存档位置必须是目录')
            if folder==result['asset_dir'] or folder in (result['config_path'],result['session_path']):raise ValueError('双宠存档目录应与配置文件、素材目录分开')
        return result

    def _check_identity(self,value,paths):
        if self.multi_workspace is None:
            check_session_definition(PetDefinition.from_record(value['definition']),paths['session_path'])
        else:
            from .multi_pet import dual_configurations
            dual_configurations(configuration_from_record(value,require_api_key=False),workspace_root=paths['multi_workspace'])

    def _checked_local(self):
        value=self.record();paths=self._paths()
        self._check_identity(value,paths)
        from .asset_inspection import inspect_asset_sources
        report=inspect_asset_sources(paths['asset_dir'])
        if value.get('expression_codes',False):
            from model_packages.desktop_pet_v1.face_expression import CODES
            if set(report['expressions'])!=set(CODES):raise ValueError('素材包未提供完整的五种表情')
        return value,paths,report

    def check_local(self):
        try:
            _value,_paths,report=self._checked_local()
            self.last_error=None
            self.status.set(f"配置、存档对应关系与{len(report['sources'])}个素材引用检查通过；未连接模型。播放兼容性在启动时验证。")
        except Exception as error:self._error(error)

    def _error(self,error):
        self.last_error=str(error);self.status.set("请检查设置："+self.last_error)

    def save(self):
        try:
            if self._creation_pending:raise ValueError("请等待背景生成完成。")
            value=self.record();paths=self._paths()
            self._require_created(value)
            self._check_identity(value,paths)
            save_configuration(paths["config_path"],value)
            if self.preferences_path is not None:
                selection=LaunchSelection(configuration=configuration_from_record(value,require_api_key=False),**paths)
                _atomic_json(self.preferences_path,selection.path_record())
            self.last_error=None;self.status.set("配置与启动位置已保存；未连接模型。")
        except Exception as error:self._error(error)

    def start(self):
        env_name=None;old_key=None;changed=False
        try:
            if self._creation_pending:raise ValueError("请等待背景生成完成。")
            value,paths,_report=self._checked_local()
            self._require_created(value)
            key=self.secret.get()
            if key:
                env_name=value["provider"].get("api_key_env")
                if not env_name:raise ValueError("a key environment name is required for the session key")
                old_key=os.environ.get(env_name);os.environ[env_name]=key;changed=True
            configuration=configuration_from_record(value)
            selection=LaunchSelection(configuration=configuration,**paths)
            save_configuration(paths["config_path"],value)
            if self.preferences_path is not None:_atomic_json(self.preferences_path,selection.path_record())
            if key and self.remember_secret.get():
                from .local_credentials import store
                store(env_name,key)
            self.result=selection;self.last_error=None;self.secret.set("");self.window.destroy()
        except Exception as error:
            if changed:
                if old_key is None:os.environ.pop(env_name,None)
                else:os.environ[env_name]=old_key
            self._error(error)

    def cancel(self):
        self.secret.set("");self.window.destroy()


def select_launch(*,config_path,session_path,trace_dir,preferences_path,asset_dir=None,multi_workspace=None):
    root=tk.Tk();root.withdraw()
    try:
        dialog=PetSetupDialog(root,config_path=config_path,session_path=session_path,
            trace_dir=trace_dir,preferences_path=preferences_path,asset_dir=asset_dir,
            **({'multi_workspace':multi_workspace} if multi_workspace is not None else {}))
        root.wait_window(dialog.window)
        if dialog.result is None:root.destroy();return None,None
        return root,dialog.result
    except Exception:
        root.destroy();raise
