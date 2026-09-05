import requests


class ServiceError(Exception):
    def __init__(self, message, status=0, retry_after=60):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class HTTP:
    def __init__(self, session=None):
        self.session = session or requests.Session()

    def request(self, method, url, **kwargs):
        try:
            response = self.session.request(method, url, timeout=(8, 20),
                                            allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise ServiceError("网络连接失败") from None
        if not 200 <= response.status_code < 300:
            labels = {400: "请求参数错误", 401: "认证失败，请检查凭据或重新登录",
                      402: "额度不足", 403: "无访问权限", 404: "对象不存在",
                      409: "远端数据冲突", 412: "远端数据已变更", 429: "请求过于频繁"}
            retry = response.headers.get("Retry-After", "60")
            retry = min(86400, max(5, int(retry))) if str(retry).isdigit() else 60
            raise ServiceError(labels.get(response.status_code, "服务返回错误"),
                               response.status_code, retry)
        if response.status_code == 204:
            return {}
        try:
            return response.json()
        except ValueError:
            raise ServiceError("服务返回无效 JSON") from None
