"""Small optional desktop scene, using existing world and shared-bowl inputs."""

from random import randint

from model_packages.desktop_pet_v1.spatial import world_objects
from model_packages.desktop_pet_v1.meal_button import REF as BUTTON


PROPS = (
    (BUTTON, '出饭按钮'),
    ('world:playground:ribbon', '蓝色缎带'),
    ('world:playground:bookmark', '星星书签'),
    ('world:playground:teacup', '小茶杯'),
    ('world:playground:bell', '猫咪铃铛'),
)
WIDTH, HEIGHT = 116, 36


class MealButton:
    def __init__(self, *, draw=None):
        self._draw = draw or (lambda: randint(2, 5))
        self._remaining = self._draw()

    def press(self, award):
        if self._remaining > 1:
            self._remaining -= 1
            return 'empty'
        if not award():
            return 'busy'
        self._remaining = self._draw()
        return 'served'


class PlaygroundProps:
    def __init__(self, x, y):
        self._bounds = {ref: [x, y+index*(HEIGHT+6), WIDTH, HEIGHT]
                        for index,(ref,_) in enumerate(PROPS)}
        self._facts = {BUTTON: '桌面上有一个出饭按钮，按动几次有机会出现一碗饭。'}
        self.button = MealButton()

    def move(self, ref, point, *, observed=True):
        bounds = self._bounds[ref]
        x,y = point
        if bounds[:2]==[x,y]:
            return False
        self._bounds[ref]=[x,y,*bounds[2:]]
        label=dict(PROPS)[ref]
        if observed:
            self._facts[ref]=f'桌面上的{label}被挪到新位置，物件左上角现在位于屏幕坐标（{x}，{y}）。'
        else:
            self._facts.pop(ref,None)
        return True

    def bounds(self, ref):
        return tuple(self._bounds[ref])

    def objects(self):
        rows=[]
        for ref,label in PROPS:
            x,y,w,h=self._bounds[ref]
            row={'ref':ref,'label':label,'point':[x+w//2,y+h//2],'bounds':[x,y,w,h]}
            if ref in self._facts:
                row['fact']=self._facts[ref]
            rows.append(row)
        return world_objects(rows)

    def press(self, award, *, observed=True):
        status=self.button.press(award)
        if status=='empty' and observed:
            self._facts[BUTTON]='出饭按钮被按下，这次没有出饭。'
        elif status=='served':
            # The shared bowl placement already publishes this occurrence.
            self._facts.pop(BUTTON,None)
        return status


def nearest_holder(point, participants):
    x,y=point
    def distance(row):
        left,top,width,height=row['bounds']
        return ((left+width/2-x)**2+(top+height/2-y)**2,row['ref'])
    return min(participants,key=distance)['ref'] if participants else None


class PlaygroundWindow:
    def __init__(self, host):
        import tkinter as tk
        self.host=host
        root=host.root
        self.props=PlaygroundProps(max(16,root.winfo_screenwidth()//2-58),
                                   max(16,root.winfo_screenheight()//2-100))
        self.original=tuple(host.scene.objects)
        self.publish_objects()
        self.windows=[]
        self.surfaces={}
        self.dragging=set()
        for ref,label in PROPS:
            surface=tk.Toplevel(root)
            self.windows.append(surface)
            self.surfaces[ref]=surface
            surface.overrideredirect(True)
            surface.attributes('-topmost',True)
            x,y,w,h=self.props.bounds(ref)
            surface.geometry(f'{w}x{h}{x:+d}{y:+d}')
            if ref==BUTTON:
                button=tk.Button(surface,text=label,command=lambda:self.press(button))
                self.button_widget=button
                button.pack(fill='both',expand=True)
                continue
            tile=tk.Label(surface,text=label,relief='groove',background='#f6efe1',cursor='fleur')
            tile.pack(fill='both',expand=True)
            self.bind_drag(tile,surface,ref)

    def bind_drag(self, tile, surface, ref):
        offset=[]
        def start(event):
            offset[:]=[event.x_root-surface.winfo_x(),event.y_root-surface.winfo_y()]
            self.dragging.add(ref)
        def move(event):
            if offset:
                surface.geometry(f'{event.x_root-offset[0]:+d}{event.y_root-offset[1]:+d}')
        def finish(event):
            if offset:
                point=(event.x_root-offset[0],event.y_root-offset[1])
                offset.clear()
                self.dragging.discard(ref)
                if self.props.move(ref,point):
                    self.publish_objects()
        tile.bind('<ButtonPress-1>',start)
        tile.bind('<B1-Motion>',move)
        tile.bind('<ButtonRelease-1>',finish)

    def publish_objects(self):
        self.host.scene.objects=world_objects((*self.original,*self.props.objects()))

    def move_from_actor(self, scene, binding):
        from model_packages.desktop_pet_v1.prop_move import check
        result=check(scene,binding)
        ref=result['ref']
        if ref in self.dragging or ref not in self.surfaces:
            raise ValueError('prop is unavailable')
        x,y,_,_=self.props.bounds(ref)
        dx,dy=(end-start for start,end in zip(result['from'],result['to']))
        x,y=x+dx,y+dy
        self.surfaces[ref].geometry(f'{x:+d}{y:+d}')
        self.props.move(ref,(x,y),observed=False)
        self.publish_objects()
        return result

    def press_from_actor(self, scene, binding):
        from model_packages.desktop_pet_v1.meal_button import check

        check(scene,binding)
        actor=binding['actor_ref']
        meal=None

        def award():
            nonlocal meal
            if any(row['portions'] for row in self.host.meal.snapshots()):
                return False
            meal=self.host.meal.start(actor)
            return True

        status=self.props.press(award,observed=False)
        self.publish_objects()
        if meal is not None:
            self.host._publish_meal()
        self._show_press(self.button_widget,status)
        return {'ref':BUTTON,'actor_ref':actor,'status':status,'meal':meal}

    def press(self, button):
        x,y,w,h=self.props.bounds(BUTTON)
        host=self.host
        participants=[]
        for window in host.windows.values():
            snapshot=host.scene.snapshot(window.configuration.definition.owner_ref)
            if snapshot is not None:
                participants=snapshot['participants']
                break
        owner=nearest_holder((x+w/2,y+h/2),participants)
        status=self.props.press(lambda:bool(owner and host.start_meal(owner,from_button=True)))
        self.publish_objects()
        self._show_press(button,status)

    def _show_press(self,button,status):
        # Tk's delayed callback has no effect on the simulation.
        button.configure(text={'empty':'这次没出饭','busy':'先吃完这碗','served':'出饭了'}[status])
        if getattr(self,'_reset_label',None) is not None:
            button.after_cancel(self._reset_label)
        self._reset_label=button.after(1000,lambda:button.configure(text='出饭按钮'))
