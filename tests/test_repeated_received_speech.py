from copy import deepcopy

from product_adapters.desktop_pet_v1.memory_archive_delivery import share_received_speech


def test_share_only_observation_and_keep_each_result():
    fragment = {'材料性质':'话语记录','原文':'放下吧','说话者':'甲','原投递指向':['乙'],
                '可见受众':['乙'],'角色取得方式':'听到','直接证明':'说过','物件ID':'cup1'}
    first = {'记忆标记':'R1','原事件片段':[fragment],'父级回应对象':['乙'],'内容接收者':'乙'}
    def occurrence(n):
        return {**deepcopy(first),'记忆标记':f'R{n}'}
    second = {**occurrence(2),'当时表达目的':'提醒','后续回执关系':[{'事件':'R5'}]}
    distinct = []
    for key, value in [('说话者','丙'),('原投递指向',['丙']),('可见受众',['甲','乙']),
                       ('角色取得方式','听转述'),('物件ID','cup2'),('材料性质','执行回执')]:
        row = occurrence(len(distinct)+3)
        row['原事件片段'][0][key] = value
        distinct.append(row)
    rows = [first, second, *distinct]
    result = share_received_speech({'相关记忆（按发生顺序）': rows})['相关记忆（按发生顺序）']
    assert result[0] == first and result[2:] == distinct
    assert result[1]['本次收到的同一发言内容参照'] == 'R1'
    assert '原事件片段' not in result[1]
    assert {k:v for k,v in result[1].items() if k!='本次收到的同一发言内容参照'} == {
        k:v for k,v in second.items() if k!='原事件片段'}
