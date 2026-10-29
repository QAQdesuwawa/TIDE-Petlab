"""Bounded textual observation, derived locally without psychological scoring."""
import json
from .interaction_delivery import project_parameters
from .trajectory_features import summarize_features
VERSION='desktop-pet-interaction-text-summary-v1'
MAX_CHARS=1200

def summarize_interaction(content):
    if content.parameters.get('capture_version')=='petlab-mouse-batch-v5':
        from .mouse_batch import summarize_batch
        return summarize_batch(content)
    p=project_parameters(content.parameters) # Verify original local evidence first.
    if 'geometry_summary' not in p:raise ValueError('text summary requires a verified mouse observation')
    phase=p['phase'];gesture=p['gesture']
    phase_text={'started':'用户刚按下，开始接触；尚未观测到后续移动或松开。',
        'ongoing':'用户仍按住角色，尚未松开。','ended':'用户已松开，交互结束。'}[phase]
    features=summarize_features(content.parameters['trajectory'],allow_point=phase=='started')
    parts=[phase_text]
    if phase!='started':
        parts.append('本段观测到角色被拖动。' if gesture=='drag' else
            '本段保持接触，未识别为拖动。' if content.parameters.get('capture_mode')=='release-only' else
            '本段保持接触，采样未见位移。')
        axes=features['axis_reversals']
        if axes[0]:parts.append('采样中出现横向往返，包含可能的指针抖动。')
        if axes[1]:parts.append('采样中出现纵向往返，包含可能的指针抖动。')
        if features['sampled_pause_ms']:parts.append('采样中出现停顿。')
        parts.append(f"本段持续{p['interval_duration_ms']/1000:g}秒，整次接触累计{p['total_duration_ms']/1000:g}秒。")
    result={'交互摘要':''.join(parts),'操作含义':p['gesture_definition']['description'].replace('body_target','起始接触部位'),
        '事实边界':'只量测本段新增作用；起始命中部位不证明持续抓扯该部位，移动路径不证明身体旋转、转圈或伤害。'}
    if p.get('body_target'):
        target=p['body_target'];result['起始部位']={'名称':target['label'],'含义':target['description']}
    if len(json.dumps(result,ensure_ascii=False))>MAX_CHARS:raise ValueError('interaction text summary exceeds 1200 characters')
    return result
