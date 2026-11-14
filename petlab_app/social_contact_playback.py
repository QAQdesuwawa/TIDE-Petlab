"""UI-thread paired pose lease, backed by a specific accepted invitation."""
from product_adapters.desktop_pet_v1.execution import ExecutionCancelled

def frame(view,task,content,index,clip):
    b=content.parameters['social_contact'];host=getattr(view,'social_contact_host',None)
    if host is None:raise ExecutionCancelled('contact host unavailable')
    try:host.check(b)
    except ValueError as error:raise ExecutionCancelled('contact invitation no longer available') from error
    peer=getattr(view,'contact_peers',{}).get(b['target'])
    if peer is None or peer.closed:raise ExecutionCancelled('contact target left')
    if not b['key'].startswith('accept_'):return
    if index==0:
        if peer.active is not None or getattr(peer,'contact_lease',None) is not None or getattr(view,'contact_lease',None) is not None:
            raise ExecutionCancelled('contact participant occupied')
        from model_packages.desktop_pet_v1 import meal_space
        try:meal_space.check(view.scene_snapshot(),b['actor'],b['target'])
        except ValueError as error:raise ExecutionCancelled('contact target out of reach') from error
        task.contact_partner=peer
        peer.contact_lease=(view,task)
        peer._stop_visual_timer()
        view.facing_target=b['target'];peer.facing_target=b['actor']
        view.gaze_target=b['target'];peer.gaze_target=b['actor']
    if getattr(peer,'contact_lease',None)!=(view,task):raise ExecutionCancelled('paired contact interrupted')
    if index<len(clip['frames']):
        other=peer.activity_clips[b['key']]
        peer._show_frame(other['frames'][min(index,len(other['frames'])-1)])

def finish(view,task,content,status):
    results=getattr(task,'contact_results',None)
    if results is None:task.contact_results=results={}
    key=content.content_id
    if key in results:return
    count=task.action_frames.get(key,0)
    if count:results[key]=view.social_contact_host.finish(task.action_id+'#'+key,
        content.parameters['social_contact'],status=status,frames=count)
    release(task)

def release(task):
    peer=getattr(task,'contact_partner',None)
    if peer is not None:
        peer.contact_lease=None;task.contact_partner=None
        if not peer.closed:peer._visual_frame(0)
