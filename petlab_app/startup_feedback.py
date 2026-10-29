"""Hidden-launch error surface only; no model, history or retry authority."""
from pathlib import Path
from datetime import datetime,timezone
import json,sys,traceback


def _hidden_launch():
    return sys.platform=='win32' and Path(sys.executable).name.lower()=='pythonw.exe'


def _run_product():
    from .desktop_app import main
    return main()


def _log_path():
    return Path(__file__).resolve().parents[1]/'output/launcher/startup-errors.jsonl'


def _category(error):
    if isinstance(error,TimeoutError):return 'timeout'
    if isinstance(error,OSError):return 'file_access'
    if isinstance(error,ValueError):return 'invalid_data'
    if isinstance(error,RuntimeError):return 'runtime'
    return 'unexpected'


def _record_failure(error):
    # Exception messages, source lines, locals and provider bodies stay out.
    record={'version':'petlab-startup-failure-v1','at_utc':datetime.now(timezone.utc).isoformat(),
        'category':_category(error),'frames':[
            {'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name}
            for frame in traceback.extract_tb(error.__traceback__)]}
    path=_log_path();path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a',encoding='utf8') as stream:
        stream.write(json.dumps(record,ensure_ascii=False,separators=(',',':'))+'\n')
    return path


def _show_failure(text):
    import tkinter as tk
    from tkinter import ttk
    root=tk.Tk();root.withdraw();choice=[None]
    try:
        root.title('TIDE · 启动失败');root.resizable(False,False)
        frame=ttk.Frame(root,padding=20);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='桌宠未能启动',font=('Microsoft YaHei UI',12,'bold')).pack(anchor='w',pady=(0,12))
        ttk.Label(frame,text=text,wraplength=480,justify='left').pack(anchor='w')
        buttons=ttk.Frame(frame);buttons.pack(fill='x',pady=(18,0))
        def setup():choice[0]='setup';root.destroy()
        ttk.Button(buttons,text='打开设置',command=setup).pack(side='left')
        ttk.Button(buttons,text='关闭',command=root.destroy).pack(side='right')
        root.bind('<Escape>',lambda event:root.destroy())
        root.bind('<Return>',lambda event:root.destroy())
        root.update_idletasks()
        x=max(0,(root.winfo_screenwidth()-root.winfo_reqwidth())//2)
        y=max(0,(root.winfo_screenheight()-root.winfo_reqheight())//2)
        root.geometry('+'+str(x)+'+'+str(y));root.deiconify();root.mainloop()
        return choice[0]
    finally:
        try:root.destroy()
        except tk.TclError:pass


def run():
    original_argv=sys.argv
    try:
        while True:
            try:return _run_product()
            except Exception as error:
                if not _hidden_launch() or '--validate' in sys.argv:raise
                hints={'timeout':'角色恢复等待超时。',
                    'file_access':'桌宠文件暂时无法读取或保存，请检查保存位置和磁盘空间。',
                    'invalid_data':'角色配置、记忆或素材未能通过检查。',
                    'runtime':'桌宠后台未能完成启动。',
                    'unexpected':'启动过程中发生了错误。'}
                text=hints[_category(error)]+'\n可点“打开设置”检查角色配置和保存位置。'
                try:path=_record_failure(error)
                except Exception:path=None
                if path is not None:text+='\n\n本地诊断记录：'+str(path)
                try:choice=_show_failure(text)
                except Exception:
                    if sys.stderr is not None:
                        try:sys.stderr.write('TIDE startup failed; open quickstart.ps1 -Setup to review configuration.\n')
                        except Exception:pass
                    return 1
                if choice!='setup':return 1
                # Only an explicit button choice re-enters the existing setup.
                # Paths remain original; no automatic retry or archive rewrite.
                sys.argv=[original_argv[0],*[arg for arg in original_argv[1:] if arg not in {'--resume','--setup'}],'--setup']
    finally:sys.argv=original_argv
