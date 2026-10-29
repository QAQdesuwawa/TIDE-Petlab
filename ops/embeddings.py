# ops/embeddings.py
"""语义嵌入（二期接线）：可插拔 embed_fn 协议 + 零依赖基线实现。

基线 = 字符二元组哈希向量（zlib.crc32 确定性，进程无关）——[拍脑袋待标定]：
零依赖、可回放，供语义检索先跑通端到端；后续可整体替换为 API embedding
或本地模型（只需实现 embed(text) -> list[float]，runner/memory 零改动）。

T5 接线：make_embedder() 工厂——EMBED_MODEL/MODEL_NAME_EMBED + API_KEYS
齐备 → agents.embed_client.EmbedClient（真实嵌入）；缺任一 → BigramEmbedder
（确定性兜底）。产品入口（GUI/CLI）显式 opt-in；runner 默认仍 BigramEmbedder
（测试/回放确定性纪律）。
"""
import math
import os
import zlib


class BigramEmbedder:
    """零依赖基线嵌入：256 维二元组哈希向量（L2 归一）。"""

    DIM = 256
    # 哈希碰撞无法承担“同义相关”的语义证明；消费者据此在无真实二元组重合时
    # 失效化该余弦分数。真实 embedding 客户端不声明这个标记。
    fallback_relevance_guard = True

    def embed(self, text):
        t = text or ""
        vec = [0.0] * self.DIM
        s = "##" + t + "##"
        for i in range(len(s) - 1):
            h = zlib.crc32(s[i:i + 2].encode("utf-8")) % self.DIM
            vec[h] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def __call__(self, text):
        return self.embed(text)


def has_exact_ngram_overlap(left, right, n=2):
    """两个原文是否拥有真实（未哈希）的连续 n-gram 交集。

    这是 fallback 嵌入的可信度闸，不是关键词表或业务规则：它只避免哈希桶碰撞、或
    过短的偶然词面重合把无关文本伪造成相关。默认二元组以兼容已有调用；召回层可
    选择更严格的 n。长度不足 n 时返回 False，宁可不召回也不误召回。
    """
    a, b = str(left or ""), str(right or "")
    width = max(1, int(n))
    if len(a) < width or len(b) < width:
        return False
    return bool({a[i:i + width] for i in range(len(a) - width + 1)}
                & {b[i:i + width] for i in range(len(b) - width + 1)})


def has_exact_bigram_overlap(left, right):
    """兼容旧调用的二元组交集别名。"""
    return has_exact_ngram_overlap(left, right, n=2)


def make_rag_embedder():
    """构造产品 F09 使用的真实 embedding provider；不可用时返回 ``None``。

    这与 ``make_embedder`` 的 legacy/offline 兼容职责故意分开：F09 只有在
    ``EMBED_MODEL`` / ``MODEL_NAME_EMBED`` 已配置且真实客户端可构造时才算
    available。Bigram 哈希不得作为静默 fallback 冒充向量 RAG。
    """
    if not (os.getenv("EMBED_MODEL") or os.getenv("MODEL_NAME_EMBED")):
        return None
    try:
        from agents.embed_client import EmbedClient
        return EmbedClient()
    except Exception:
        return None

def make_embedder():
    """产品入口用工厂：真实嵌入（API）→ 确定性基线兜底。

    opt-in 条件：EMBED_MODEL 或 MODEL_NAME_EMBED 已配置且 API_KEYS 存在。
    任一缺失/构造失败 → BigramEmbedder（行为与旧版逐位一致）。
    """
    if os.getenv("EMBED_MODEL") or os.getenv("MODEL_NAME_EMBED"):
        try:
            from agents.embed_client import EmbedClient
            return EmbedClient()
        except Exception:
            pass
    return BigramEmbedder()
