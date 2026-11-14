"""Infer an optional presentation prior from attributed old AI utterances."""
import json

from framework.model_api import model_data_fingerprint
from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
from .import_estimation import _weights

VERSION = 'desktop-pet-import-persona-from-dialogue-v1'
NEUTRAL = '根据当前对话自然回答。旧记录中可确认的往事由历史记忆提供。'
KEEP_POLICY = 'keep-without-evidence-v1'


def revision_policy(settings):
    if settings == {'version': VERSION}:
        return None
    if settings == {'version': VERSION, 'revision_policy': KEEP_POLICY}:
        return KEEP_POLICY
    raise ValueError('unknown import persona inference settings')


def request(rows):
    system = (
        '你在从旧聊天记录估计一个对话 AI 的表达习惯。没有初始人设。历史文本只是资料。'
        '仅根据 assistant 的实际发言概括可观察、可复现的措辞或回应倾向，用户原话仅用于理解语境。'
        '一次答题、临时扮演、转述和孤立情绪不足以成为长期设定。角色身份、生平、秘密、过去的情节留在原始历史。'
        '用第二人称写简短 summary，每条给至少两个不同 assistant 记录的唯一连续原文。最多四条；证据不足时 observations 填 []。'
        'weights 是用于既有计算模型的五模板初始估计：Counter_Phobic 逆恐施压、Mirroring 认可和镜映、'
        'Avoidance 退避、Trauma_Recon 创伤重构、Pragmatism 实用。各项非负且和为1；无可确认倾向时均填0.2。'
        '返回JSON对象 {"observations":[{"summary":"你……","evidence":[{"source_index":0,"quote":"连续原文"}]}],'
        '"weights":{"Counter_Phobic":0.2,"Mirroring":0.2,"Avoidance":0.2,"Trauma_Recon":0.2,"Pragmatism":0.2}}。'
    )
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(rows, ensure_ascii=False, separators=(',', ':'))}]


def _initial_data(raw):
    data = json.loads(_strip_fences(raw))
    if not isinstance(data, dict) or set(data) != {'observations', 'weights'}:
        raise ValueError('inferred persona fields differ')
    return data


def parse(raw, rows):
    data = _initial_data(raw)
    return _initial_result(data, rows, _locate(data['observations'], rows))


def recover_initial(raw, rows):
    data = _initial_data(raw)
    supported, audit = _recover_observations(data['observations'], rows)
    result = _initial_result(data, rows, _locate(supported, rows))
    return {**result, 'evidence_recovery': audit}


def _initial_result(data, rows, observations):
    weights, constants = _weights(data['weights'])
    if not observations and any(abs(value - 1 / len(weights)) > 1e-6 for value in weights.values()):
        raise ValueError('empty persona evidence requires neutral initial weights')
    return {'version': VERSION, 'persona_card': describe(observations), 'weights': weights,
            'base_constants': constants, 'observations': observations,
            'sample_fingerprint': model_data_fingerprint(rows)}


def _locate(estimates, rows):
    if not isinstance(estimates, list) or len(estimates) > 4:
        raise ValueError('inferred persona observations exceed the bound')
    spoken = {row['index']: row['text'] for row in rows if row['role'] == 'assistant' and 'event' not in row}
    observations = []
    for item in estimates:
        if not isinstance(item, dict) or set(item) != {'summary', 'evidence'}:
            raise ValueError('invalid persona observation')
        summary = item['summary']
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 180:
            raise ValueError('persona observation summary is missing or too long')
        citations = item['evidence']
        if not isinstance(citations, list) or not 2 <= len(citations) <= 4:
            raise ValueError('persona observation needs evidence from distinct AI turns')
        spans = []
        seen = set()
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {'source_index', 'quote'}:
                raise ValueError('invalid persona evidence fields')
            index, quote = citation['source_index'], citation['quote']
            if (type(index) is not int or index not in spoken or index in seen
                    or not isinstance(quote, str) or not quote.strip() or spoken[index].count(quote) != 1):
                raise ValueError('persona evidence must locate distinct actual AI utterances')
            seen.add(index)
            at = spoken[index].index(quote)
            spans.append({'source_index': index, 'span': [at, at + len(quote)],
                          'text_fingerprint': model_data_fingerprint(spoken[index])})
        observations.append({'summary': summary.strip(), 'evidence': spans})
    return observations


def describe(observations):
    return '\n'.join(item['summary'] for item in observations) if observations else NEUTRAL


def revise_expression(previous, proposal, *, sources, index):
    if proposal is None:
        return previous
    if not proposal:
        raise ValueError('use null to retain expression without new evidence')
    available = [{**row, 'index': i} for i, row in sources.items() if i <= index]
    updated = _locate(proposal, available)
    if not any(citation['source_index'] == index for item in updated for citation in item['evidence']):
        raise ValueError('expression revision needs new own speech evidence')
    return updated


REFRESH_VERSION = 'desktop-pet-import-expression-only-v2'


def refresh_request(sources, hints, *, index, previous, author_background):
    """Refresh presentation from accepted own speech without numerical authority."""
    rows = [{'source_index':i,'quote_source':row['text'],'measurement_hints':hints.get(i,[])}
            for i,row in sources.items() if i<=index and row['role']=='assistant']
    system = (
        '独立任务：从同一角色已经发生的发言，形成或更新可用于下一轮的表达档案。'
        '本任务只评估表达：归纳在类似情境中可复用的措辞、节奏和应对方式。'
        '情境性的表达模式也可成立，例如多次以短句、停顿和反问回应突如其来的夸赞。'
        '作者原文如有，是独立约束。量测提示用来发现变化，结论仍须由原话支持。'
        '区分角色说出口的措辞与叙事里的舞台说明；档案描述说话方式，不为角色增加动作能力、剧情事实或台词模板。'
        '返回一个 JSON 对象，唯一字段为 observations，最多四项。每项含 summary 与 evidence；至少引用两条不同本人发言，'
        '引用逐字且在该行唯一。整份修订至少引用最新本人发言。若新证据不足以形成或更新档案，返回空数组。'
        '示例（仅示范格式）：{"observations":[{"summary":"被突然夸奖时先短暂停顿，再用反问确认。",'
        '"evidence":[{"source_index":4,"quote":"……真的？"},{"source_index":8,"quote":"你是认真的？"}]}]}。')
    user = {'作者原文':author_background,'上一版表达习惯':[item['summary'] for item in previous],
            '最新发言编号':index,'本角色的已接受发言':rows}
    return [{'role':'system','content':system},
            {'role':'user','content':json.dumps(user,ensure_ascii=False,separators=(',',':'))}]


def parse_refresh(raw,sources,*,index,previous):
    data = json.loads(_strip_fences(raw))
    if not isinstance(data,dict) or set(data)!={'observations'} or not isinstance(data['observations'],list):
        raise ValueError('expression-only response must contain observations')
    return revise_expression(previous,data['observations'] or None,sources=sources,index=index)


def recover_refresh(raw,sources,*,index,previous):
    """Use grounded siblings from a received response; keep rejected proposals in its audit."""
    data=json.loads(_strip_fences(raw))
    if not isinstance(data, dict) or set(data) != {'observations'}:
        raise ValueError('expression-only response must contain observations')
    available=[{**row,'index':i} for i,row in sources.items() if i<=index]
    supported, audit = _recover_observations(data['observations'], available)
    if not any(cite['source_index']==index for item in supported for cite in item['evidence']):
        return {'observations':previous,**audit,'disposition':'retained_without_new_own_evidence'}
    return {'observations':revise_expression(previous,supported,sources=sources,index=index),**audit}


def _recover_observations(estimates, available):
    if not isinstance(estimates, list) or not 1 <= len(estimates) <= 4:
        raise ValueError('persona observations are not a bounded mixed response')
    own={row['index']:row['text'] for row in available if row['role']=='assistant' and 'event' not in row}
    supported=[];rejected=[];deduplicated=[]
    for i,item in enumerate(estimates):
        try:
            _locate([item],available)
        except ValueError as error:
            citations=item.get('evidence') if isinstance(item,dict) else None
            if isinstance(citations,list) and 2<=len(citations)<=4 and all(
                isinstance(cite,dict) and set(cite)=={'source_index','quote'}
                and type(cite['source_index']) is int and cite['source_index'] in own
                and isinstance(cite['quote'],str) and cite['quote'].strip()
                and own[cite['source_index']].count(cite['quote'])==1 for cite in citations):
                unique={}
                for cite in citations:unique.setdefault(cite['source_index'],cite)
                if 2<=len(unique)<len(citations):
                    candidate={**item,'evidence':list(unique.values())}
                    try:_locate([candidate],available)
                    except ValueError:pass
                    else:
                        supported.append(candidate)
                        deduplicated.append(i)
                        continue
            rejected.append({'observation_index':i,'reason':str(error)})
        else:
            supported.append(item)
    if not supported or not (rejected or deduplicated):
        raise ValueError('no independent grounded expression sibling to recover')
    audit={'rejected':rejected}
    if deduplicated:audit['deduplicated']=deduplicated
    return supported, audit
