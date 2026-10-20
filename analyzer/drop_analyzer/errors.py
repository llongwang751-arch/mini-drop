"""分析链路的固定错误类别，分别表示可重试失败和已失去作业租约。"""

class AnalysisError(Exception):
    """结构化的分析错误；message 不能包含原始 profile 数据。"""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LeaseLost(AnalysisError):
    def __init__(self):
        super().__init__("ANALYSIS_LEASE_LOST", "Analysis lease ownership changed")
