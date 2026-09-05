import requests
import socket


class ServiceError(Exception):
    def __init__(self, message, status=0, retry_after=60, kind='SERVICE_ERROR'):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after
        self.kind = kind
        self.stage = 'request'

    def diagnostic(self):
        return '{} {} HTTP {}: {}'.format(self.stage, self.kind, self.status, str(self))


def connection_error(exc):
    # Inspect exception types only; URLs and credentials can occur in their text.
    if isinstance(exc, requests.exceptions.SSLError):
        return ServiceError('TLS 证书验证或握手失败，请检查设备日期和证书', kind='TLS_ERROR')
    if isinstance(exc, requests.exceptions.ConnectTimeout):
        return ServiceError('连接超时', kind='CONNECT_TIMEOUT')
    if isinstance(exc, requests.exceptions.ReadTimeout):
        return ServiceError('读取超时', kind='READ_TIMEOUT')
    if isinstance(exc, requests.exceptions.Timeout):
        return ServiceError('连接或读取超时', kind='TIMEOUT')
    if isinstance(exc, requests.exceptions.ProxyError):
        return ServiceError('代理连接失败', kind='PROXY_ERROR')
    pending, seen = [exc], set()
    while pending and len(seen) < 40:
        item = pending.pop()
        if id(item) in seen:
            continue
        seen.add(id(item))
        if isinstance(item, socket.gaierror):
            return ServiceError('DNS 解析失败，请检查 API Host 和网络', kind='DNS_ERROR')
        pending.extend(x for x in (getattr(item, '__cause__', None), getattr(item, '__context__', None),
                                  getattr(item, 'reason', None)) if isinstance(x, BaseException))
        pending.extend(x for x in getattr(item, 'args', ()) if isinstance(x, BaseException))
    return ServiceError('网络连接失败，请检查网络和 API Host', kind='CONNECTION_ERROR')


class HTTP:
    def __init__(self, session=None):
        self.session = session or requests.Session()

    def request(self, method, url, **kwargs):
        try:
            response = self.session.request(method, url, timeout=(8, 20),
                                            allow_redirects=False, **kwargs)
        except requests.RequestException as exc:
            raise connection_error(exc) from None
        if not 200 <= response.status_code < 300:
            labels = {400: "请求参数错误", 401: "认证失败，请检查凭据或重新登录",
                      402: "额度不足", 403: "无访问权限", 404: "对象不存在",
                      409: "远端数据冲突", 412: "远端数据已变更", 429: "请求过于频繁"}
            retry = response.headers.get("Retry-After", "60")
            retry = min(86400, max(5, int(retry))) if str(retry).isdigit() else 60
            raise ServiceError(labels.get(response.status_code, "服务返回错误"),
                               response.status_code, retry, kind='HTTP_ERROR')
        if response.status_code == 204:
            return {}
        try:
            return response.json()
        except ValueError:
            raise ServiceError("服务返回无效 JSON", kind='INVALID_JSON') from None
