"""Device authorization and Graph tasks, without an SDK or client secret."""
import time
import threading
from urllib.parse import quote, urlsplit
from .network import HTTP, ServiceError

AUTH = "https://login.microsoftonline.com/consumers/oauth2/v2.0/"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "Tasks.ReadWrite Mail.ReadWrite User.Read offline_access"
LEGACY_SCOPES = "Tasks.ReadWrite offline_access"
MAIL_SCOPES = SCOPES  # Compatibility for older callers.


class Microsoft:
    def __init__(self, client_id, store, http=None):
        self.client_id, self.store = client_id, store
        self.http = http or HTTP()
        self.auth_lock = threading.Lock()
        self.auth_cancelled = False

    def begin_mail_login(self):
        return self.begin_login()

    def begin_login(self):
        self.upgrade_identity = None
        old = self.store.read("token.json", {})
        if old:
            # Determine the old identity before consent; never replace on uncertainty.
            identity = old.get("account_id")
            if not identity:
                try:
                    identity = self.graph("GET", "/me?$select=id")["id"]
                except (ServiceError, KeyError):
                    # Older Graph tokens may not permit /me. The token was obtained
                    # directly over TLS; decode only for identity comparison, never auth.
                    import base64
                    import json
                    try:
                        segment = self.token().split(".")[1]
                        identity = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))).get("oid")
                    except Exception:
                        identity = None
            if not identity:
                raise ServiceError("无法验证旧账户，请先提交待办操作，再注销并重新登录微软账户")
            self.upgrade_identity = identity
        return self._begin_device_login()

    def cancel_login(self):
        with self.auth_lock:
            self.auth_cancelled = True

    def _begin_device_login(self):
        with self.auth_lock:
            self.auth_cancelled = False
        if not self.client_id or self.client_id.startswith("YOUR-"):
            raise ServiceError("请先在配置中填写微软 Client ID")
        return self.http.request("POST", AUTH + "devicecode", data={
            "client_id": self.client_id, "scope": SCOPES})

    def poll_login(self, code, mail=True):
        # OAuth polling errors are protocol states, not raw diagnostic output.
        try:
            response = self.http.session.post(AUTH + "token", data={
                "client_id": self.client_id, "device_code": code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                timeout=(8, 20), allow_redirects=False)
            data = response.json()
        except Exception:
            raise ServiceError("登录连接失败") from None
        error = data.get("error")
        if error in ("authorization_pending", "slow_down"):
            return error
        if error or response.status_code != 200:
            raise ServiceError("登录已失效或被拒绝，请重试")
        if not {'Tasks.ReadWrite', 'Mail.ReadWrite', 'User.Read'}.issubset(set(data.get('scope', '').split())):
            raise ServiceError('未授予完整待办与邮箱权限，原登录已保留')
        profile = self.http.request('GET', GRAPH + '/me?$select=id,mail,userPrincipalName',
                                    headers={'Authorization': 'Bearer ' + data['access_token']})
        identity = profile.get('id')
        if not identity or (getattr(self, 'upgrade_identity', None) and identity != self.upgrade_identity):
            raise ServiceError('授权账户与原待办账户不同，原登录已保留')
        data.update(account_id=identity, account_label=profile.get('mail') or profile.get('userPrincipalName', '微软邮箱'))
        with self.auth_lock:
            if self.auth_cancelled:
                return "cancelled"
            self.save_token(data)
        return "success"

    def save_token(self, data):
        previous = self.store.read("token.json", {})
        if not data.get("access_token"):
            raise ServiceError("登录响应缺少令牌")
        token = {"access_token": data["access_token"],
                 "refresh_token": data.get("refresh_token", previous.get("refresh_token", "")),
                 "expires_at": time.time() + float(data.get("expires_in", 3600)),
                 "client_id": self.client_id,
                 "scope": data.get("scope", previous.get("scope", LEGACY_SCOPES)),
                 "account_id": data.get("account_id", previous.get("account_id", "")),
                 "account_label": data.get("account_label", previous.get("account_label", ""))}
        self.store.write("token.json", token)

    def token(self, force=False):
        token = self.store.read("token.json", {})
        if token.get("client_id") != self.client_id:
            raise ServiceError("请登录微软账户")
        if not force and token.get("expires_at", 0) > time.time() + 120:
            return token["access_token"]
        if not token.get("refresh_token"):
            raise ServiceError("请重新登录微软账户")
        try:
            data = self.http.request("POST", AUTH + "token", data={
                "client_id": self.client_id, "grant_type": "refresh_token",
                "refresh_token": token["refresh_token"], "scope": token.get("scope", LEGACY_SCOPES)})
        except ServiceError as exc:
            if exc.status in (400, 401):
                raise ServiceError("微软登录已过期，请重新登录", 401) from None
            raise
        self.save_token(data)
        return data["access_token"]

    def graph(self, method, path, **kwargs):
        url = path if path.startswith("https://") else GRAPH + path
        self.validate_graph_url(url)
        extra_headers = kwargs.pop("headers", {})
        return self._graph_request(method, url, extra_headers, **kwargs)

    @staticmethod
    def validate_graph_url(url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "graph.microsoft.com" or not parsed.path.startswith("/v1.0/"):
            raise ServiceError("拒绝无效的 Graph 分页地址")
    def _graph_request(self, method, url, extra_headers, **kwargs):
        for attempt in range(2):
            headers = dict(extra_headers)
            headers["Authorization"] = "Bearer " + self.token(force=attempt == 1)
            try:
                return self.http.request(method, url, headers=headers, **kwargs)
            except ServiceError as exc:
                if exc.status != 401 or attempt:
                    raise

    def pages(self, path):
        result, seen = [], set()
        while path:
            if path in seen:
                raise ServiceError("Graph 分页循环")
            seen.add(path)
            data = self.graph("GET", path)
            if not isinstance(data.get("value"), list):
                raise ServiceError("待办数据格式错误")
            result.extend(data["value"])
            path = data.get("@odata.nextLink")
        return result

    @staticmethod
    def task_path(list_id, task_id=None):
        result = "/me/todo/lists/" + quote(list_id, safe="") + "/tasks"
        return result + "/" + quote(task_id, safe="") if task_id else result

    def sync(self):
        lists = self.pages("/me/todo/lists")
        cache = self.store.read("todo.json", {"lists": [], "tasks": {}})
        ids = {entry["id"] for entry in lists}
        cache["lists"] = lists
        cache["tasks"] = {k: v for k, v in cache["tasks"].items() if k in ids}
        failures = []
        for entry in lists:
            try:
                cache["tasks"][entry["id"]] = self.pages(self.task_path(entry["id"]))
                entry["synced"] = time.time()
            except ServiceError as exc:
                entry["error"] = str(exc)
                failures.append(exc)
                if exc.status == 429:
                    break
        if not failures:
            cache["synced"] = time.time()
        self.store.write("todo.json", cache)
        if failures:
            raise failures[0]
        return cache

    def enqueue(self, list_id, task):
        def add(queue):
            if not any(q["list_id"] == list_id and q["task_id"] == task["id"] for q in queue):
                queue.append({"list_id": list_id, "task_id": task["id"],
                              "modified": task.get("lastModifiedDateTime"), "state": "pending"})
        self.store.update("outbox.json", [], add)

    def flush(self):
        for item in self.store.read("outbox.json", []):
            if item["state"] != "pending":
                continue
            path = self.task_path(item["list_id"], item["task_id"])
            try:
                remote = self.graph("GET", path)
                if remote.get("status") != "completed":
                    if remote.get("lastModifiedDateTime") != item["modified"]:
                        raise ServiceError("任务已在其他端改变，请刷新后确认", 409)
                    # Use an ETag when supplied by Graph; otherwise the compare above is best effort.
                    headers = {"If-Match": remote["@odata.etag"]} if remote.get("@odata.etag") else {}
                    self.graph("PATCH", path, json={"status": "completed"}, headers=headers)
                def mark(cache):
                    for task in cache.get("tasks", {}).get(item["list_id"], []):
                        if task["id"] == item["task_id"]:
                            task["status"] = "completed"
                self.store.update("todo.json", {}, mark)
                self.store.update("outbox.json", [], lambda q: q.__setitem__(slice(None), [
                    x for x in q if (x["list_id"], x["task_id"]) != (item["list_id"], item["task_id"])]))
            except ServiceError as exc:
                if exc.status in (403, 404, 409, 412):
                    def conflict(queue):
                        for q in queue:
                            if (q["list_id"], q["task_id"]) == (item["list_id"], item["task_id"]):
                                q.update(state="conflict", error=str(exc))
                    self.store.update("outbox.json", [], conflict)
                else:
                    raise


def task_views(cache):
    return [("@important", "重要"), ("@planned", "计划内")] + [
        (entry["id"], entry["displayName"]) for entry in cache.get("lists", [])]


def tasks_for(cache, view_id):
    result = []
    for list_id, tasks in cache.get("tasks", {}).items():
        for task in tasks:
            if task.get("status") == "completed":
                continue
            if (view_id == "@important" and task.get("importance") == "high" or
                view_id == "@planned" and task.get("dueDateTime") or view_id == list_id):
                result.append((list_id, task))
    return sorted(result, key=lambda pair: ((pair[1].get("dueDateTime") or {}).get("dateTime", "9999"), pair[1].get("title", "")))


def due_date(task):
    # Graph To Do deadlines are calendar dates in their declared timezone, not UTC instants.
    return (task.get("dueDateTime") or {}).get("dateTime", "")[:10]
