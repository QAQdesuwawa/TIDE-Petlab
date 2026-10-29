"""隔离测试夹具的来源标记。

生产会话从不写入该来源；运行时消费者只需知道一条记录是否为合成夹具，从而避免它
未经召回直接进入普通生成上下文。这里不解析任何测试正文，也不参与状态或检索排序。
"""

SYNTHETIC_FIXTURE_SOURCE = "synthetic_fixture"
DEMO_BACKGROUND_SOURCE = "demo_prelude"


def is_synthetic_fixture(record):
    return isinstance(record, dict) and record.get("source") == SYNTHETIC_FIXTURE_SOURCE


def is_non_dialogue_seed(record):
    """可进记忆层、但绝不能冒充聊天短尾的背景种子。"""
    return (isinstance(record, dict)
            and (bool(record.get("non_dialogue_seed"))
                 or record.get("source") in {SYNTHETIC_FIXTURE_SOURCE,
                                               DEMO_BACKGROUND_SOURCE}))
