"""中文嵌入模型适配。

选型依据（`decision-register.md` H17）：采用 ``BAAI/bge-small-zh``。
实测规格（2026-10-04，HuggingFace 官方元数据）：

===========================  =========================
架构                         BertModel
hidden_size（向量维度）      512
max_seq_length              512
modules                     Transformer → Pooling → Normalize
可下载权重                  仅 ``pytorch_model.bin``（**无 safetensors**）
===========================  =========================

两个实测得到的关键结论：

1. **内置 chromadb EF 对中文无效**（``all-MiniLM-L6-v2`` 是英文模型），
   本模块是替代方案。详见 ``docs/rag-verification.md`` §2.1。
2. 该模型含 ``2_Normalize`` 模块，**输出已 L2 归一化**，因此配合
   ``cosine`` 空间时等价于点积，检索行为可预期。

**部署注意**：实测 HuggingFace 直连不通（HTTP 000），须配置代理或
预下载权重到本地后离线使用——这直接关系 ``deployment-operations.md`` §5
的断网演示要求。权重文件体积须记录，属决策登记表 E4（供应链审查）范围。

⚠️ **权重安全**：该模型仓库只提供 ``pytorch_model.bin``（PyTorch pickle
格式），不存在 safetensors 版本。加载时必须使用
``SentenceTransformer(..., trust_remote_code=False)``，且**仅从可信来源
获取**（``security-privacy.md`` §5 要求核验来源、授权与完整性）。
"""

from __future__ import annotations

import logging
import os
from typing import Sequence

from .errors import EmbeddingUnavailableError

logger = logging.getLogger(__name__)

#: 模型标识。写入向量库 metadata 以便追溯（`knowledge-base.md` §3、§7）。
#: 变更此值即意味着 embedding 模型变更，**必须完整重建索引**。
BGE_SMALL_ZH = "BAAI/bge-small-zh"

#: 实测的向量维度（来自官方 config.json 的 hidden_size）。
BGE_SMALL_ZH_DIM = 512

#: 实测的最大序列长度（来自官方 sentence_bert_config.json）。
BGE_SMALL_ZH_MAX_SEQ = 512


class SentenceTransformerEmbedding:
    """基于 sentence-transformers 的嵌入提供者。

    延迟加载模型：构造对象不下载权重，避免在无网环境下 import 即失败
    （`deployment-operations.md` §6 要求配置缺失时快速失败，
    但模型下载失败应发生在实际使用时而非导入时）。
    """

    def __init__(
        self,
        model_name: str = BGE_SMALL_ZH,
        *,
        cache_folder: str | None = None,
        local_files_only: bool = False,
        device: str | None = None,
    ) -> None:
        self.model_name = model_name
        # 与 model_name 一致，满足 §3「索引须绑定 embedding 模型标识」
        self.model_id = model_name
        self._cache_folder = cache_folder
        self._local_files_only = local_files_only
        self._device = device
        self._model = None

    def _ensure_loaded(self):
        """延迟加载模型，失败转为受控错误。"""
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailableError(
                "嵌入模型依赖缺失，请先安装 sentence-transformers。",
                detail=f"ImportError: {exc}",
            ) from None

        try:
            self._model = SentenceTransformer(
                self.model_name,
                cache_folder=self._cache_folder,
                local_files_only=self._local_files_only,
                device=self._device,
                # 不执行仓库中的自定义代码。bge 系列不需要此权限，
                # 开启反而扩大供应链风险（security-privacy.md §5）。
                trust_remote_code=False,
            )
        except Exception as exc:
            # 常见原因：断网且无本地缓存、磁盘空间不足、模型名错误。
            # 不回显可能含路径/凭据的原始信息。
            logger.warning(
                "嵌入模型加载失败: model=%s type=%s", self.model_name, type(exc).__name__
            )
            raise EmbeddingUnavailableError(
                "嵌入模型加载失败，请检查网络、本地缓存或模型标识。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from None

        logger.info("嵌入模型已加载: model=%s", self.model_name)
        return self._model

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """把文本转为向量。

        Args:
            texts: 待嵌入文本。

        Returns:
            向量列表，每个元素为 512 维浮点列表。

        Raises:
            EmbeddingUnavailableError: 模型不可用或推理失败。
                **不回退到其他模型**——换模型会使已有向量失效
                （`knowledge-base.md` §3）。
        """
        if not texts:
            return []
        model = self._ensure_loaded()
        try:
            vectors = model.encode(
                list(texts),
                # bge 系列官方建议对检索查询加指令前缀
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            logger.warning("嵌入推理失败: type=%s", type(exc).__name__)
            raise EmbeddingUnavailableError(
                "嵌入推理失败，知识库检索已降级。",
                detail=f"{type(exc).__name__}: {exc}",
            ) from None

        return [[float(x) for x in v] for v in vectors]

    def warmup(self) -> int:
        """预加载模型并返回向量维度。

        用于服务启动时提前触发下载/加载，避免首个用户请求等待
        （`architecture.md` §6 的可观测性要求区分冷启动与正常延迟）。

        Raises:
            EmbeddingUnavailableError: 加载失败。
        """
        model = self._ensure_loaded()
        return int(model.get_sentence_embedding_dimension())


def probe_query_prefix() -> str:
    """返回 bge 系列的检索查询指令前缀。

    BGE 中文模型官方建议：**查询侧**加
    ``为这个句子生成表示以用于检索相关文章：`` 前缀，
    **文档侧不加**，可提升检索准确率。

    本函数返回该前缀，由调用方决定是否拼接——因为前缀会改变向量，
    入库与查询两侧必须保持约定一致。
    """
    return "为这个句子生成表示以用于检索相关文章："


def resolve_local_model_path(explicit: str | None = None) -> str | None:
    """解析本地权重目录（离线部署用）。

    优先级：显式参数 > 环境变量 ``XUEZHI_EMBEDDING_PATH``。

    返回 ``None`` 表示未配置本地路径，调用方应走在线下载或报错。
    """
    return explicit or os.environ.get("XUEZHI_EMBEDDING_PATH") or None


__all__ = [
    "BGE_SMALL_ZH",
    "BGE_SMALL_ZH_DIM",
    "BGE_SMALL_ZH_MAX_SEQ",
    "SentenceTransformerEmbedding",
    "probe_query_prefix",
    "resolve_local_model_path",
]
