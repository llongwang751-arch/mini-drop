"""供应商临时故障的有界退避与进程内熔断；每次实际请求仍须重新向 Go 预留模型预算。"""

import ssl
import threading
import time
from email.utils import parsedate_to_datetime

from .wire import ProtocolError


class RetryableProviderFailure(BaseException):
    """内部控制信号只携带分类和受限等待时间，不保留响应正文、URL 或凭据。"""

    def __init__(self, category, retry_after=None):
        self.category = category
        self.retry_after = retry_after


def retry_after_seconds(value):
    if not value or len(value) > 128:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (ValueError, TypeError, OverflowError):
            return None
    if not 0 <= seconds < float("inf"):
        return None
    return min(seconds, 2.0)


def permanent_transport_error(failure):
    cause = failure
    for _ in range(8):
        if isinstance(cause, (ssl.SSLCertVerificationError, ssl.CertificateError)):
            return True
        if cause is None:
            break
        cause = cause.__cause__ or cause.__context__
    return False


class ProviderCircuit:
    """同一可信 endpoint/model 在本 runner 进程内共享：五次临时失败熔断三十秒，之后只放行一次探测。"""

    def __init__(self):
        self.failures = 0
        self.opened_until = 0.0
        self.probing = False
        self.lock = threading.Lock()

    def before(self):
        with self.lock:
            now = time.monotonic()
            if self.opened_until > now or self.probing:
                raise ProtocolError("AGENT_PROVIDER_CIRCUIT_OPEN")
            if self.opened_until:
                self.probing = True

    def success(self):
        with self.lock:
            self.failures = 0
            self.opened_until = 0.0
            self.probing = False

    def failure(self):
        with self.lock:
            self.failures += 1
            if self.failures >= 5 or self.probing:
                self.opened_until = time.monotonic() + 30.0
            self.probing = False

    def abandon(self):
        # 取消或硬错误不是供应商恢复证据，也不把探测锁永久留住。
        with self.lock:
            self.probing = False


_circuits = {}
_circuits_lock = threading.Lock()


def provider_circuit(endpoint, model):
    key = (endpoint, model)
    with _circuits_lock:
        if key not in _circuits:
            if len(_circuits) >= 16:
                raise ProtocolError("AGENT_PROVIDER_CIRCUIT_OPEN")
            _circuits[key] = ProviderCircuit()
        return _circuits[key]
