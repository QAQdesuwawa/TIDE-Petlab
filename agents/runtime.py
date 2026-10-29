"""运行时 Agent 装配：GUI、CLI 与未来服务入口共享的唯一工厂。

入口层只负责 UI/参数解析；本模块只决定本次运行使用演示 Agent 还是 LLM
Agent。它不读取业务状态，也不参与 prompt 或 Runner 编排。
"""
from agents.demo_client import demo_mode_enabled, make_demo_agents


AGENT_TAGS = ("labor", "extractor", "responder", "preference", "ledger")
DEFAULT_PROVIDER_TAGS = ("responder",)
_PROVIDER_TAGS = frozenset(AGENT_TAGS + ("semantic",))


def _provider_tags(providers):
    """校验一次运行明确请求的 provider；缺省只保留回复生成。"""
    requested = DEFAULT_PROVIDER_TAGS if providers is None else tuple(providers)
    unknown = set(requested) - _PROVIDER_TAGS
    if unknown:
        raise ValueError("未知运行时 provider: " + ", ".join(sorted(unknown)))
    # responder 是唯一的正常对话生成边界，不能被增强配置意外移除。
    return tuple(dict.fromkeys(("responder",) + tuple(requested)))


def make_llm_agents(
    client_class=None, providers=None, *, semantic_codec=None,
):
    """仅构造本次运行显式需要的 LLM provider。

    默认只返回 responder。labor/preference 是显式开局能力；semantic、extractor、
    ledger 是增强或兜底能力，不能因“客户端已创建”自动进入每轮主路径。
    """
    if client_class is None:
        from agents.llm_client import LLMClient
        client_class = LLMClient
    requested = _provider_tags(providers)
    if "semantic" in requested and semantic_codec is None:
        raise ValueError(
            "requested semantic provider requires an explicit codec")
    agents = {}
    for tag in AGENT_TAGS:
        if tag not in requested:
            continue
        client = client_class()
        client.agent_tag = tag
        agents[f"{tag}_agent"] = client
    if "semantic" in requested:
        # 语义投影职责独立于延迟 extractor；只有显式启用时才构造网络 provider。
        semantic_client = client_class()
        semantic_client.agent_tag = "semantic"
        from agents.semantic_projector import LLMSemanticProjector
        agents["semantic_projector"] = LLMSemanticProjector(
            semantic_client, codec=semantic_codec)
    return agents


def build_agents(
    env=None, client_class=None, providers=None, *, semantic_codec=None,
):
    """按显式 provider 集合装配运行时；默认不装配增强线。"""
    requested = _provider_tags(providers)
    if demo_mode_enabled(env):
        agents = make_demo_agents(tags=tuple(tag for tag in requested if tag != "semantic"))
        if "semantic" in requested:
            agents["semantic_projector"] = None
        return agents
    return make_llm_agents(
        client_class=client_class,
        providers=requested,
        semantic_codec=semantic_codec,
    )
