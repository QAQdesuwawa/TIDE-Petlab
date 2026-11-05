"""Meaning of private intention progress; no selection or state calculation."""
VERSION='cx67-private-intention-presentation-v1'
PHASE_LABELS={'formed':'已形成回应意图','planned':'已编排，执行情况另列',
              'effect':'存在实际执行回执','completed':'本次尝试已结束',
              'interrupted':'本次尝试中止','abandoned':'本次选择不继续表现'}
REASON_LABELS={'partial_execution':'本次执行中断，已发生部分保留，未执行部分仍是计划',
               'executed':'实际执行已返回','pending':'有新事实到达，待后续重新编排',
               'cancelled':'本次表现已取消','quiet':'本次选择安静',
               'no_language_realizer':'本次没有可执行的表现实现器',
               'technical_failure':'技术执行失败，已发生部分以实际回执为准'}
