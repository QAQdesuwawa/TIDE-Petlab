"""Ephemeral desktop presentation; no event, model or formal clock authority."""
from __future__ import annotations
import math
import time
from collections import deque
import sys
import tkinter as tk
import weakref

def reading_duration_ms(text,*,minimum_ms=8000,maximum_ms=30000,per_char_ms=180):
    """Presentation time only: retain short speech and allow longer text to be read."""
    count=sum(not ch.isspace() for ch in text)
    return min(maximum_ms,minimum_ms+max(0,count-20)*per_char_ms)


def speech_progress_ms(text):
    """Readable occupancy of a segment, not its idle bubble retention.

    Only spaces in an already authorized sequence advance on this duration.
    The final bubble retains the normal longer dismissal/hover behavior.
    This display pacing never grants an Actor a turn or advances dynamic Tick.
    """
    count=sum(not ch.isspace() for ch in text)
    return min(30000,max(1600,700+count*180))


class PetSpeechBubble:
    _instances=weakref.WeakSet()
    KEY="#ff00fe"
    FILL="#f1f8ff"
    OUTLINE="#8eafd7"

    def __init__(self,root,*,position,dismiss_after_ms=8000):
        self.root=root;self.position=position;self.dismiss_after_ms=dismiss_after_ms
        self.reading_ms_per_char=180;self.maximum_dismiss_ms=30000
        self.mode="hidden";self.animation_job=None;self.dismiss_job=None
        self._generation=0;self._closed=False;self._phase=0;self._tail_on_top=False
        self.window=tk.Toplevel(root);self.window.withdraw();self.window.overrideredirect(True)
        self.window.configure(bg=self.KEY);self.window.wm_attributes("-topmost",True)
        if sys.platform=="win32":self.window.wm_attributes("-transparentcolor",self.KEY)
        self.canvas=tk.Canvas(self.window,bg=self.KEY,highlightthickness=0,borderwidth=0)
        self.canvas.pack()
        self.label=tk.Label(self.canvas,text="",bg=self.FILL,fg="#365578",wraplength=240,
            borderwidth=0,font=("Microsoft YaHei UI",10),justify="left")
        # This bounded UI readback is not a memory source or another event log.
        self.history=deque(maxlen=128);self._speech_sequence=0
        self.history_window=None;self.history_text=None;self._reviewing=False;self._hovered=False
        self._remaining_ms=0;self._deadline=0
        for widget in (self.window,self.canvas,self.label):
            widget.bind("<Enter>",lambda event:self._pause_dismiss(),add="+")
            widget.bind("<Leave>",self._leave,add="+")
        self._width=88;self._body_height=44
        self._placement=None;self._tail_x=None
        self._instances.add(self)

    def place(self,x,y,*,anchor_x):
        self._placement=(int(x),int(y),int(anchor_x))
        self._reflow()

    @classmethod
    def _reflow(cls):
        from .bubble_layout import arrange
        bubbles=[b for b in cls._instances if not b._closed and b.mode!='hidden' and b._placement is not None]
        if not bubbles:return
        rows=[(id(b),*b._placement[:2],b._width,b._body_height+10) for b in bubbles]
        screen=(bubbles[0].root.winfo_screenwidth(),bubbles[0].root.winfo_screenheight())
        layout=arrange(rows,screen)
        for b in bubbles:
            x,y,_,_=layout[id(b)]
            tip=max(18,min(b._width-18,b._placement[2]-x))
            if tip!=b._tail_x:
                b._tail_x=tip
                b._move_tail()
            b.window.geometry(f'+{x}+{y}')

    def _move_tail(self):
        # Relayout must not destroy/recreate embedded text windows mid-paint.
        center=self._tail_x if self._tail_x is not None else self._width//2
        top=12 if self._tail_on_top else 2
        bottom=(10 if self._tail_on_top else 0)+self._body_height-2
        tail=([center-7,top+1,center,1,center+7,top+1] if self._tail_on_top
              else [center-7,bottom-1,center,self._body_height+8,center+7,bottom-1])
        self.canvas.coords('bubble-tail',*tail)
        edge=top+1 if self._tail_on_top else bottom-1
        self.canvas.coords('bubble-tail-edge',center-6,edge,center+6,edge)

    def _cancel_jobs(self):
        self._generation+=1
        for attribute in ("animation_job","dismiss_job"):
            job=getattr(self,attribute)
            if job is not None:
                try:self.root.after_cancel(job)
                except tk.TclError:pass
            setattr(self,attribute,None)

    def set_tail(self,on_top):
        if self._tail_on_top!=on_top:
            self._tail_on_top=on_top;self._draw()

    def _draw(self):
        self.canvas.delete('bubble-shell','bubble-tail','bubble-tail-edge','typing-dot')
        width,height=self._width,self._body_height
        offset=10 if self._tail_on_top else 0
        self.canvas.configure(width=width,height=height+10)
        left,right,top,bottom=2,width-2,offset+2,offset+height-2
        radius=16
        points=[left+radius,top,right-radius,top,right,top,right,top+radius,
            right,bottom-radius,right,bottom,right-radius,bottom,left+radius,bottom,
            left,bottom,left,bottom-radius,left,top+radius,left,top]
        self.canvas.create_polygon(points,smooth=True,splinesteps=24,fill=self.FILL,
            outline=self.OUTLINE,width=2,tags="bubble-shell")
        center=self._tail_x if self._tail_x is not None else width//2
        if self._tail_on_top:tail=[center-7,top+1,center,1,center+7,top+1]
        else:tail=[center-7,bottom-1,center,height+8,center+7,bottom-1]
        self.canvas.create_polygon(tail,fill=self.FILL,outline=self.OUTLINE,width=2,tags="bubble-tail")
        edge=top+1 if self._tail_on_top else bottom-1
        self.canvas.create_line(center-6,edge,center+6,edge,fill=self.FILL,width=3,tags='bubble-tail-edge')
        if self.mode=="speech":
            self._place_text_window('speech-text',self.label,18,offset+13)
        elif self.mode in {"typing","contact"}:
            self.canvas.itemconfigure('speech-text',state='hidden')
            color="#6f9dd0" if self.mode=="typing" else "#a0bfd9"
            for index in range(3):self.canvas.create_oval(0,0,1,1,fill=color,outline="",tags=("typing-dot",f"dot-{index}"))
            self._draw_dots()

    def _place_text_window(self,tag,widget,x,y):
        if self.canvas.find_withtag(tag):
            self.canvas.coords(tag,x,y);self.canvas.itemconfigure(tag,state='normal')
        else:self.canvas.create_window(x,y,window=widget,anchor='nw',tags=tag)

    def _draw_dots(self):
        offset=10 if self._tail_on_top else 0
        for index in range(3):
            x=self._width/2+(index-1)*16
            y=offset+22-4*math.sin(self._phase-index*.8)
            self.canvas.coords(f"dot-{index}",x-3.5,y-3.5,x+3.5,y+3.5)

    def _present(self):
        self.window.update_idletasks();self.position(self.window)
        self.window.deiconify();self.window.lift();self.window.update_idletasks()

    def show_contact(self):
        # Local contact feedback has no send or model authority.
        if self.mode in {"speech","typing"}:return
        self._show_dots("contact")

    def show_typing(self):
        if self.mode=="speech":return
        self._show_dots("typing")

    def _show_dots(self,mode):
        if self._closed or self.mode==mode:return
        self._cancel_jobs();self.mode=mode;self._phase=0
        self._width=88;self._body_height=44
        self._draw();self._present();generation=self._generation
        def animate():
            if self._closed or generation!=self._generation or self.mode!=mode:return
            self._phase+=.65;self._draw_dots()
            self.animation_job=self.root.after(80,animate)
        self.animation_job=self.root.after(80,animate)

    def accepts_speech(self):
        # Existing execution/source guards still decide whether an output may show.
        return not self._closed

    def show_speech(self,text):
        if self._closed:return
        self._cancel_jobs();self.mode="speech"
        self.history.append(text);self._speech_sequence+=1
        self.label.configure(text=text);self.label.update_idletasks()
        self._width=max(88,self.label.winfo_reqwidth()+36)
        self._body_height=self.label.winfo_reqheight()+30
        self._draw();self._present()
        if self.history_text is not None:self._append_history(text)
        self.last_display_duration_ms=reading_duration_ms(text,minimum_ms=self.dismiss_after_ms,
            maximum_ms=self.maximum_dismiss_ms,per_char_ms=self.reading_ms_per_char)
        self._remaining_ms=self.last_display_duration_ms
        self._arm_dismiss()

    def _arm_dismiss(self):
        if self._closed or self.mode!="speech" or self._hovered or self._reviewing:return
        if self.dismiss_job is not None:return
        generation=self._generation
        self._deadline=time.monotonic()+self._remaining_ms/1000
        def dismiss():
            self.dismiss_job=None
            if not self._closed and generation==self._generation and self.mode=="speech":self.hide()
        self.dismiss_job=self.root.after(max(1,int(self._remaining_ms)),dismiss)

    def _pause_dismiss(self):
        self._hovered=True
        if self.dismiss_job is not None:
            self._remaining_ms=max(1,(self._deadline-time.monotonic())*1000)
            self.root.after_cancel(self.dismiss_job);self.dismiss_job=None

    def _resume_dismiss(self):
        self._hovered=False;self._arm_dismiss()

    def _leave(self,event=None):
        def check():
            if self._closed:return
            widget=self.window.winfo_containing(self.window.winfo_pointerx(),self.window.winfo_pointery())
            if widget is None or widget.winfo_toplevel()!=self.window:self._resume_dismiss()
        if not self._closed:self.root.after_idle(check)

    def show_history(self):
        if self._closed:return
        self._reviewing=True
        hovered=self._hovered;self._pause_dismiss();self._hovered=hovered
        if self.history_window is None:
            from tkinter.scrolledtext import ScrolledText
            self.history_window=tk.Toplevel(self.root);self.history_window.title("最近发言")
            self.history_window.geometry("380x320");self.history_window.wm_attributes("-topmost",True)
            self.history_text=ScrolledText(self.history_window,wrap="word",font=("Microsoft YaHei UI",10),
                bg=self.FILL,fg="#365578",padx=12,pady=12)
            self.history_text.pack(fill="both",expand=True)
            self.history_text.insert("end","\n\n".join(self.history));self.history_text.configure(state="disabled")
            self.history_window.protocol("WM_DELETE_WINDOW",self.hide_history)
            self.history_window.bind("<Escape>",lambda event:self.hide_history())
        self.history_window.deiconify();self.history_window.lift()

    def _append_history(self,text):
        reader=self.history_text;at_end=reader.yview()[1]>=.999
        reader.configure(state="normal");reader.insert("end","\n\n"+text)
        # Keep the reader bounded too; removing old UI text never removes formal history.
        if self._speech_sequence>128:
            reader.delete("1.0","end");reader.insert("end","\n\n".join(self.history))
        reader.configure(state="disabled")
        if at_end:reader.see("end")

    def hide_history(self):
        self._reviewing=False
        if self.history_window is not None:self.history_window.withdraw()
        self._arm_dismiss()

    def stop_contact(self):
        if self.mode=="contact":self.hide()

    def stop_typing(self):
        if self.mode=="typing":self.hide()

    def hide(self):
        self._cancel_jobs();self.mode="hidden"
        if not self._closed:self.window.withdraw()
        self._reflow()
        callback=getattr(self,'on_hide',None)
        if callback is not None:callback()

    def close(self):
        if self._closed:return
        self.hide();self._closed=True
        if self.history_window is not None:self.history_window.destroy()
