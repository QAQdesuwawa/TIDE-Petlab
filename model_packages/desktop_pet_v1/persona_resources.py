"""Read-only reverse rendering from accepted five-template weights and own speech."""
import json
from pathlib import Path
import re

from agents.labor import build_labor_messages
from model_packages.cx_six_seven_v1.preference_parameters import validate_preference_weights
from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences

VERSION='desktop-pet-reverse-persona-resources-v1'
FIELDS=('语气','文风','口癖')


def _spoken(text):
    # The source uses matched single asterisks for roleplay narration.
    return re.sub(r'(?<!\*)\*(?!\*)[^*]+\*(?!\*)','\n',text)


def request(weights,*,author,observations,own_lines):
    labor,_=build_labor_messages('')
    definitions=labor.split('## 五模板语义',1)[1].split('\n## ',1)[0].strip()
    template=(Path(__file__).resolve().parents[2]/'prompts/persona_card.md').read_text('utf8')
    system=(
        '把已确认的五模板倾向渲染为人格卡的可消费表达资源。作者原文负责身份和设定；'
        '五权重只提供相对回应倾向，语气和文风以实际表达证据约束。相同权重允许多种合理措辞。'
        '口癖是本人在不同发言中反复使用的具体措辞或称呼，至少列两个不同的来源行号；'
        '使用原文短语，避免把通用词、叙事旁白或一次性的台词当作口癖。可用口癖为空数组。'
        '遵循原人格卡栏目，输出 JSON 对象，包含语气、文风、口癖三个字段。'
        '语气和文风各写一到两句供 Writer 参考，使用倾向描述，给当前语境留出发挥空间。'
        '口癖示例（仅格式）：[{"phrase":"先慢慢说","source_indices":[2,8]}]。'
        '身份、禁区和打样保持来源边界；本合同只生成上述三项，不补写身份、经历或现成对白。'
        '\n\n## 五模板语义'+definitions+'\n\n'+template)
    value={'已结算五模板权重':validate_preference_weights(weights),'作者原文':author,
           '已确认的表达观察':observations,
           '本人发言':[{'source_index':i,'text':_spoken(line)} for i,line in own_lines.items()]}
    return system,json.dumps(value,ensure_ascii=False,separators=(',',':'))


def parse(raw,own_lines):
    data=json.loads(_strip_fences(raw))
    if not isinstance(data,dict) or set(data)!=set(FIELDS):
        raise ValueError('reverse persona resource fields differ')
    out={}
    for key in FIELDS[:2]:
        value=data[key]
        if not isinstance(value,str) or not value.strip() or len(value)>300:
            raise ValueError('reverse persona style exceeds its contract')
        out[key]=value.strip()
    if not isinstance(data['口癖'],list) or len(data['口癖'])>4:
        raise ValueError('reverse persona habits exceed their contract')
    habits=[];rejected=[];seen=set()
    for i,item in enumerate(data['口癖']):
        if not isinstance(item,dict) or set(item)!={'phrase','source_indices'}:
            raise ValueError('reverse persona habit fields differ')
        phrase=item['phrase'];indices=item['source_indices']
        if (not isinstance(phrase,str) or not 0<len(phrase)<=60 or phrase!=phrase.strip()
                or not any(c.isalnum() for c in phrase) or phrase in seen
                or not isinstance(indices,list) or not 2<=len(indices)<=4
                or any(type(j) is not int or j not in own_lines or phrase not in _spoken(own_lines[j]) for j in indices)
                or len(set(indices))!=len(indices)):
            rejected.append(i)
            continue
        seen.add(phrase)
        habits.append({'phrase':phrase,'source_indices':indices})
    return {**out,'口癖':habits,'rejected_habit_indices':rejected}
