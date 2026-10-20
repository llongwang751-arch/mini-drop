class AnalysisError(Exception):
    """Safe, structured failure; message must never contain raw profile data."""

    def __init__(self, code: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class LeaseLost(AnalysisError):
    def __init__(self):
        super().__init__("ANALYSIS_LEASE_LOST", "Analysis lease ownership changed")
