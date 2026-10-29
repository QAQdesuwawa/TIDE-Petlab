"""Operational UI hints from the current provider outcome; no retry authority."""
from collections.abc import Mapping


def failure_summary(record):
    """The gateway calls this only after saving its original terminal receipt."""
    if record.get('status')!='failed':return None
    failed=record['execution'].get('failure') or record.get('codec_parse') or {}
    timing=record.get('host_timing',{})
    start=timing.get('started_at_unix_ms');end=timing.get('transport_finished_at_unix_ms')
    return {'version':'petlab-provider-failure-display-v1',
        'invocation_id':record['invocation_id'],'operation_kind':record['operation_kind'],
        'failure_kind':failed.get('failure_kind','invalid_response'),
        'elapsed_ms':end-start if type(start) is int and type(end) is int else None}


def format_product_error(error,gateway):
    """Never infer no UI effect, server root cause, or permission to redispatch."""
    failure=getattr(gateway,'last_failure',None)
    if not isinstance(failure,Mapping):return str(error)
    kind=failure.get('failure_kind');hint=None
    if isinstance(kind,str) and kind.startswith('http_') and kind[5:].isdigit():
        status=int(kind[5:])
        if 500<=status<=599:hint=f'模型服务暂时不可用（HTTP {status}）。'
        elif status in {401,403}:hint=f'模型服务未接受访问凭据（HTTP {status}），请检查密钥和访问权限。'
        elif status==429:hint='模型服务请求受限（HTTP 429），请检查服务额度或稍后继续。'
        else:hint=f'模型请求未被接受（HTTP {status}），请检查模型名称和连接配置。'
    elif kind=='transport_unavailable':hint='模型连接中断或请求超时。'
    elif kind in {'invalid_wire_json','invalid_wire_shape','invalid_semantic_projection',
                  'incomplete_text_completion','missing_response_model','invalid_response'}:
        hint='模型返回内容不符合本轮输出格式。'
    if hint is None:hint='本次模型请求失败。'
    return hint+'\n详细信息：'+str(error)
