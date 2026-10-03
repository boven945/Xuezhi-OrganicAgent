"""知识检索层的受控错误类型。

`docs/interface-contract.md` §5 要求错误可机器读、不泄露堆栈或密钥。
本层对应的分类是"知识检索不可用"与"输入无效"。
"""

from __future__ import annotations


class RAGError(Exception):
    """知识检索层受控错误基类。

    Attributes:
        code: 稳定错误码。
        user_message: 面向用户的说明，不含内部细节。
        retryable: 是否值得重试。
    """

    code = "rag_internal_error"
    retryable = False

    def __init__(
        self,
        user_message: str,
        *,
        detail: str | None = None,
        code: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(user_message)
        self.user_message = user_message
        self.detail = detail
        if code is not None:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class EmbeddingUnavailableError(RAGError):
    """嵌入模型不可用。

    ``docs/architecture.md`` §6 要求各组件有独立失败路径。
    嵌入不可用时**不得**回退到"无向量检索"并伪装正常，
    应明确报错，由上层决定是否降级为纯文本答复。
    """

    code = "rag_embedding_unavailable"
    retryable = True


class IndexNotReadyError(RAGError):
    """索引不存在或未发布。

    `knowledge-base.md`` §3：索引须经评估验收后才发布带版本号。
    未发布的索引不得用于生产检索。
    """

    code = "rag_index_not_ready"


class InvalidDocumentError(RAGError):
    """知识片段不符合准入要求。

    `knowledge-base.md`` §2：无来源、授权状态不明、超纲或存在明显错误的
    内容不得进入生产索引。此类错误在**入库前**拦截。
    """

    code = "rag_invalid_document"


class RetrievalError(RAGError):
    """检索执行失败（存储层异常等）。"""

    code = "rag_retrieval_failed"
    retryable = True


__all__ = [
    "RAGError",
    "EmbeddingUnavailableError",
    "IndexNotReadyError",
    "InvalidDocumentError",
    "RetrievalError",
]
