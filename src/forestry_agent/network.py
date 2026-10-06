from __future__ import annotations

import codecs
import hashlib
import http.client
import ipaddress
import json
import queue
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from .context import utf8_window
from .contracts import Failure
from .resources import native_path, version
from .store import encode, now, uid

MAX_BYTES = 2 * 1024 * 1024


def search_request(provider, base, query, page=1):
    if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 10:
        raise Failure("SCHEMA_INVALID", "搜索页码须为1–10")
    if provider == "baidu_html":
        params = {"wd": query}
        if page > 1:
            params["pn"] = (page-1)*10
        url = base.rstrip("/") + "/s?" + urlencode(params)
    else:
        params = {"q": query, "format": "json" if provider == "searxng" else "rss"}
        if page > 1:
            if provider != "searxng":
                raise Failure("PRECONDITION_MISSING", "当前RSS提供商未验证分页，请使用支持分页的连接器", 422)
            params["pageno"] = page
        url = base.rstrip("/") + "/search?" + urlencode(params)
    destinations = [url_parts(base)[3]]
    if provider == "bing_rss" and base == "https://www.bing.com":
        destinations.append("https://cn.bing.com")
    return url, destinations


def search_results(provider, text):
    if provider == "baidu_html":
        parser = BaiduResults()
        parser.feed(text)
        if not parser.results:
            if 'location.href.replace("https://", "http://")' in text:
                raise Failure("PERMISSION_DENIED", "搜索提供商要求通过脚本降级HTTPS；未执行脚本或发送HTTP请求", 403)
            raise Failure("EXECUTION_FAILED", "百度页面没有可验证的普通搜索结果；可能为验证码、错误或页面结构变化", 422)
        return parser.results
    if provider == "searxng":
        try:
            items = json.loads(text)["results"]
            if not isinstance(items, list) or any(not isinstance(r, dict) for r in items[:10]):
                raise TypeError("invalid results")
        except (ValueError, KeyError, TypeError) as error:
            raise Failure("EXECUTION_FAILED", "搜索连接器没有返回合格results数组", 422) from error
        return [{"title": str(r.get("title", ""))[:500], "url": str(r.get("url", ""))[:8000], "snippet": TextPage.extract(str(r.get("content", "")))[:1500]} for r in items[:10]]
    if "<!DOCTYPE" in text.upper() or "<!ENTITY" in text.upper():
        raise Failure("PERMISSION_DENIED", "搜索XML包含不允许的实体声明", 403)
    try:
        root = ElementTree.fromstring(text)
        if root.tag != "rss" or root.find("channel") is None:
            raise ValueError("missing RSS channel")
    except (ElementTree.ParseError, ValueError) as error:
        raise Failure("EXECUTION_FAILED", "搜索连接器没有返回合格RSS", 422) from error
    return [{"title": r.findtext("title", "")[:500], "url": r.findtext("link", "")[:8000], "snippet": TextPage.extract(r.findtext("description", ""))[:1500]} for r in root.findall("./channel/item")[:10]]


def decode_text(body, content_type):
    from email.message import Message
    metadata = Message()
    metadata["content-type"] = content_type
    charset = metadata.get_content_charset() or "utf-8"
    try:
        codec = codecs.lookup(charset)
        if not codec._is_text_encoding:
            raise LookupError("non-text codec")
        return body.decode(charset, errors="replace")
    except (LookupError, ValueError) as error:
        raise Failure("EXECUTION_FAILED", "网页声明了不支持的文本编码", 422) from error


def url_parts(url):
    if not isinstance(url, str) or len(url) > 8000 or any(ord(c) < 32 or c.isspace() for c in url) or "\\" in url:
        raise Failure("SCHEMA_INVALID", "URL格式不合法")
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme == "https" else 80)
        host = (parts.hostname or "").encode("idna").decode().lower().rstrip(".")
    except (ValueError, UnicodeError) as error:
        raise Failure("SCHEMA_INVALID", "URL主机或端口不合法") from error
    if parts.scheme not in {"https", "http"} or not host or parts.username is not None or parts.password is not None:
        raise Failure("PERMISSION_DENIED", "仅接受无凭证的HTTP(S) URL", 403)
    if port not in {80, 443}:
        raise Failure("PERMISSION_DENIED", "公开网页仅允许80/443端口", 403)
    display_host = f"[{host}]" if ":" in host else host
    authority = display_host + (f":{port}" if port != (443 if parts.scheme == "https" else 80) else "")
    normalized = urlunsplit((parts.scheme, authority, parts.path or "/", parts.query, ""))
    return normalized, host, port, f"{parts.scheme}://{authority}"


class TextPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.hidden, self.words = 0, []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "template"}:
            self.hidden += 1
        if not self.hidden and tag in {"p", "br", "div", "li", "h1", "h2", "h3", "tr"}:
            self.words.append("\n")

    def handle_endtag(self, tag):
        if tag in {"script", "style", "template"} and self.hidden:
            self.hidden -= 1

    def handle_data(self, data):
        if not self.hidden:
            self.words.append(data)

    @classmethod
    def extract(cls, html):
        parser = cls()
        parser.feed(html)
        return "\n".join(line.strip() for line in "".join(parser.words).splitlines() if line.strip())


class BaiduResults(HTMLParser):
    """Read organic result containers and their declared source URLs, never ad links.

    A bounded HTML adapter, not an official JSON API. Unknown pages fail closed.
    """
    void_tags = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.results, self.stack = [], []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if len(self.results) >= 10:
            return
        attributes = dict(attrs)
        classes = set(attributes.get("class", "").split())
        if self.current is None:
            if tag != "div" or not {"result", "c-container"} <= classes or "EC_result" in classes:
                return
            try:
                source = url_parts(attributes.get("mu", ""))[0]
            except Failure:
                return
            self.current = {"url": source, "title": [], "snippet": []}
            self.stack = []
        if tag not in self.void_tags:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if self.current is None or tag not in self.stack:
            return
        position = len(self.stack) - 1 - self.stack[::-1].index(tag)
        del self.stack[position:]
        if not self.stack:
            title = " ".join("".join(self.current["title"]).split())[:500]
            snippet = " ".join("".join(self.current["snippet"]).split())[:1500]
            if title and not any(r["url"] == self.current["url"] for r in self.results):
                self.results.append({"title": title, "url": self.current["url"], "snippet": snippet})
            self.current = None

    def handle_data(self, data):
        if self.current is None or any(tag in self.stack for tag in ("script", "style", "template")):
            return
        if "h3" in self.stack:
            self.current["title"].append(data)
        self.current["snippet"].append(data)


class PublicHTTP:
    """Resolve once per hop, reject every non-global address, connect only to the pinned IP.

    No environment proxy, cookies, credential headers, automatic redirects or subresources.
    TLS still verifies the original hostname, not the numeric address.
    """
    def __init__(self, resolver=None, exchange=None, *, timeout_seconds=40):
        self.resolver = resolver or socket.getaddrinfo
        self.exchange = exchange
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _exchange(url, host, port, address, guard, deadline):
        scheme = urlsplit(url).scheme
        class PinnedConnection(http.client.HTTPConnection):
            active_socket = None
            def connect(self):
                guard(url)
                self.sock = socket.create_connection((address, port), timeout=max(0.01, min(20, deadline - time.monotonic())))
                self.active_socket = self.sock
                if scheme == "https":
                    guard(url)
                    self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=host)
                self.active_socket = self.sock
            def send(self, data):
                if self.sock is None:
                    self.connect()
                guard(url)
                self.sock.settimeout(max(0.01, min(20, deadline - time.monotonic())))
                return super().send(data)
        connection = PinnedConnection(host, port, timeout=20)
        def abort():
            if connection.active_socket:
                try:
                    connection.active_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            connection.close()
        watchdog = threading.Timer(max(0.01, deadline - time.monotonic()), abort)
        watchdog.daemon = True
        watchdog.start()
        try:
            parts = urlsplit(url)
            target = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            connection.request("GET", target, headers={"User-Agent": "ForestryUAV-Agent/0.1 public-reader",
                "Accept": "text/html,application/json,application/rss+xml,text/plain;q=0.9", "Accept-Encoding": "identity"})
            response = connection.getresponse()
            headers = {k.lower(): v for k, v in response.getheaders()}
            if headers.get("content-encoding", "identity").lower() not in {"identity", ""}:
                raise Failure("PRECONDITION_MISSING", "服务器未提供未压缩响应", 422)
            if int(headers.get("content-length", 0)) > MAX_BYTES:
                raise Failure("RESOURCE_EXHAUSTED", "网页超过2 MiB上限", 422)
            body, start = bytearray(), time.monotonic()
            while len(body) <= MAX_BYTES:
                block = response.read1(min(65536, MAX_BYTES + 1 - len(body)))
                if not block:
                    break
                body.extend(block)
                if time.monotonic() - start > 20 or time.monotonic() >= deadline:
                    raise Failure("RESOURCE_EXHAUSTED", "网页读取总时限已用尽", 422)
            return response.status, headers, bytes(body)
        finally:
            watchdog.cancel()
            connection.close()

    def get(self, url, *, guard=lambda _: None, allowed_origins=None):
        hops, deadline = [], time.monotonic() + self.timeout_seconds
        for _ in range(4):
            url, host, port, origin = url_parts(url)
            if allowed_origins is not None and origin not in allowed_origins:
                raise Failure("PERMISSION_DENIED", "重定向目的地超出授权范围", 403)
            guard(url)  # Before DNS: hostnames themselves may contain private data.
            resolved = queue.Queue(maxsize=1)
            def resolve(host=host, port=port, resolved=resolved):
                try:
                    resolved.put(self.resolver(host, port, type=socket.SOCK_STREAM))
                except (OSError, ValueError, RuntimeError, Failure) as error:
                    resolved.put(error)
            threading.Thread(target=resolve, daemon=True).start()
            try:
                records = resolved.get(timeout=max(0.01, min(10, deadline - time.monotonic())))
            except queue.Empty as error:
                raise Failure("EXECUTION_FAILED", "DNS解析超时；尚未发送HTTP载荷", 422) from error
            if isinstance(records, BaseException):
                raise records
            addresses = sorted({r[4][0] for r in records}, key=lambda a: ipaddress.ip_address(a).version)
            if not addresses or any(not ipaddress.ip_address(address).is_global or ipaddress.ip_address(address).is_multicast or ipaddress.ip_address(address).is_reserved for address in addresses):
                raise Failure("PERMISSION_DENIED", "拒绝私有、回环、链路本地或混合DNS地址", 403)
            guard(url)
            if time.monotonic() >= deadline:
                raise Failure("RESOURCE_EXHAUSTED", "公开读取总时限已用尽", 422)
            try:
                status, headers, body = self.exchange(url, host, port, addresses[0]) if self.exchange else self._exchange(url, host, port, addresses[0], guard, deadline)
            except http.client.HTTPException as error:
                raise Failure("EXECUTION_FAILED", "公开服务器HTTP协议错误", 422) from error
            if len(body) > MAX_BYTES:
                raise Failure("RESOURCE_EXHAUSTED", "网页超过2 MiB上限", 422)
            if time.monotonic() >= deadline:
                raise Failure("RESOURCE_EXHAUSTED", "公开读取总时限已用尽", 422)
            hops.append({"url": url, "address": addresses[0], "status": status})
            if status in {301, 302, 303, 307, 308}:
                if "location" not in headers:
                    raise Failure("EXECUTION_FAILED", "重定向缺少Location", 422)
                next_url = urljoin(url, headers["location"])
                if urlsplit(url).scheme == "https" and urlsplit(next_url).scheme != "https":
                    raise Failure("PERMISSION_DENIED", "拒绝HTTPS降级重定向", 403)
                url = next_url
                continue
            if status != 200:
                raise Failure("EXECUTION_FAILED", f"公开服务器返回HTTP {status}", 422)
            return {"url": url, "headers": headers, "body": body, "hops": hops, "observed_at": now()}
        raise Failure("RESOURCE_EXHAUSTED", "重定向次数超出上限", 422)


class Network:
    def __init__(self, engine, http=None):
        self.engine, self.store = engine, engine.store
        self.http = http or PublicHTTP()

    def scope(self, state, kind, value, page=1):
        if kind == "web.search":
            provider = state["config_snapshot"].get("search", {})
            if not provider:
                raise Failure("PRECONDITION_MISSING", "当前任务未冻结搜索连接器，请新建任务", 422)
            base = provider["base_url"]
            url, destinations = search_request(provider["provider"], base, value, page)
        else:
            url = url_parts(value)[0]
            destinations = [url_parts(url)[3]]
        sources = []
        for ref in state["input_refs"]:
            path = self.engine.resources.resolve(ref, task_id=state["task_id"])
            sources.append({"resource_id": ref, "version": version(path)})
        request = {"kind": kind, "value": value, "url": url, "destinations": destinations,
            "spec_revision": state["spec_revision"], "sources": sources, "method": "GET", "max_bytes": MAX_BYTES}
        if kind == "web.search" and page > 1:
            request["page"] = page
        return request, hashlib.sha256(encode(request).encode()).hexdigest()

    def list(self, task_id):
        self.store.task(task_id)
        with self.store.transaction() as db:
            return [json.loads(r["payload"]) for r in db.execute("SELECT payload FROM approvals WHERE task_id=? ORDER BY rowid", (task_id,))]

    def admit(self, deps, action_id, request, digest, approval_id):
        with self.store.transaction() as db:
            state = self.store.read_task(db, deps.task_id)
            if deps.assignment_id:
                raise Failure("PERMISSION_DENIED", "当前子角色网络上限为none", 403)
            if state["spec_revision"] != deps.revision or state["pause_requested"] or state["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "控制状态或修订阻止网络请求", 409)
            if state["config_snapshot"].get("context_classification", "private") == "public":
                return None
            row = db.execute("SELECT payload FROM approvals WHERE id=? AND task_id=?", (approval_id, deps.task_id)).fetchone() if approval_id else db.execute("SELECT payload FROM approvals WHERE task_id=? AND json_extract(payload,'$.request_sha256')=? AND status IN ('pending','approved','rejected') ORDER BY rowid DESC LIMIT 1", (deps.task_id, digest)).fetchone()
            if row:
                record = json.loads(row["payload"])
                if record["request_sha256"] != digest or record["spec_revision"] != deps.revision:
                    raise Failure("VERSION_CONFLICT", "授权请求摘要或来源版本不匹配", 409)
                if record["status"] == "approved" and datetime.fromisoformat(record["expires_at"]) > datetime.now(UTC):
                    record.update(status="consumed", consumed_at=now(), consumed_action_id=action_id)
                    db.execute("UPDATE approvals SET status='consumed',payload=? WHERE id=?", (encode(record), record["approval_id"]))
                    self.store.event(db, deps.task_id, "approval.consumed", {"approval_id": record["approval_id"], "action_id": action_id})
                    return record["approval_id"]
                if record["status"] != "pending":
                    raise Failure("PERMISSION_DENIED", "授权已拒绝、过期、撤销或消费", 403)
            else:
                if approval_id:
                    raise Failure("PERMISSION_DENIED", "审批ID不属于当前任务", 403)
                record = {"schema_version": 1, "approval_id": uid(), "task_id": deps.task_id,
                    "spec_revision": deps.revision, "request_sha256": digest, "request": request,
                    "status": "pending", "created_at": now(), "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                    "authorizing_user_event_id": None, "consumed_action_id": None}
                db.execute("INSERT INTO approvals VALUES(?,?,?,?,?,NULL)", (record["approval_id"], deps.task_id, deps.revision, "pending", encode(record)))
                self.store.event(db, deps.task_id, "approval.required", record)
            return {"kind": "approval_required", "approval_id": record["approval_id"], "request_sha256": digest,
                "message": "当前上下文可能含私密信息；请求尚未发送，请等待用户核对精确载荷和目的地。"}

    def resolve(self, task_id, approval_id, payload):
        with self.store.transaction() as db:
            replay = self.store.replay(db, payload["request_id"], {"kind": "approval.resolve", "task_id": task_id, "approval_id": approval_id, **payload})
            if replay is not None:
                return replay
            state = self.store.read_task(db, task_id)
            row = db.execute("SELECT payload FROM approvals WHERE id=? AND task_id=?", (approval_id, task_id)).fetchone()
            if not row:
                raise Failure("NOT_FOUND", "审批不存在", 404)
            record = json.loads(row["payload"])
            if state["spec_revision"] != payload["expected_spec_revision"] or record["spec_revision"] != state["spec_revision"] or record["request_sha256"] != payload["request_sha256"]:
                raise Failure("VERSION_CONFLICT", "审批请求或规范已变化", 409)
            if payload["verdict"] != "revoke":
                if state["status"] in {"completed", "failed", "cancelled", "publishing"} or state["cancellation_requested"]:
                    raise Failure("VERSION_CONFLICT", "终态、封存或已取消任务不能批准新请求", 409)
                for source in record["request"]["sources"]:
                    if version(self.engine.resources.resolve(source["resource_id"], task_id=task_id, db=db)) != source["version"]:
                        raise Failure("VERSION_CONFLICT", "审批前输入版本已变化，须重新提出请求", 409)
            if payload["verdict"] != "revoke" and (record["status"] != "pending" or datetime.fromisoformat(record["expires_at"]) <= datetime.now(UTC)):
                raise Failure("VERSION_CONFLICT", "审批已处理或过期", 409)
            event = self.store.user_event(db, "approval_resolution", {"task_id": task_id, "approval_id": approval_id, **payload})
            record.update(status={"approve": "approved", "reject": "rejected", "revoke": "revoked"}[payload["verdict"]], authorizing_user_event_id=event, resolved_at=now())
            if state["status"] not in {"completed", "failed", "cancelled", "publishing"} and not state["cancellation_requested"]:
                prompt = "用户已通过审批接口处理网络请求。请读取当前审批状态；仅approved且范围、版本、有效期匹配的请求可以使用approval_id执行，已消费的请求不得重发。拒绝或撤销后请说明影响并继续可完成的工作。\n" + encode({"approval_id": approval_id, "request_sha256": record["request_sha256"], "verdict": payload["verdict"]})
                record["continuation_turn_id"] = self.store.enqueue_turn(db, state, prompt, payload["request_id"], event)
                if not state["pause_requested"] and state["status"] != "paused" and state["status"] != "active":
                    state.update(status="created", blockers=[])
                self.store.save_task(db, state, state["state_version"])
            db.execute("UPDATE approvals SET status=?,payload=?,user_event_id=? WHERE id=?", (record["status"], encode(record), event, approval_id))
            self.store.event(db, task_id, "approval.resolved", record)
            return self.store.receipt(db, payload["request_id"], {"kind": "approval.resolve", "task_id": task_id, "approval_id": approval_id, **payload}, record)

    def execute(self, deps, action_id, kind, value, approval_id=None, page=1):
        state = self.store.task(deps.task_id)
        request, digest = self.scope(state, kind, value, page)
        admission = self.admit(deps, action_id, request, digest, approval_id)
        if isinstance(admission, dict):
            return admission
        def guard(_):
            current = self.store.task(deps.task_id)
            if current["spec_revision"] != deps.revision or current["pause_requested"] or current["cancellation_requested"]:
                raise Failure("EXECUTION_STOPPED", "控制状态或修订阻止发送", 409)
            if admission:
                grant = next(a for a in self.list(deps.task_id) if a["approval_id"] == admission)
                if grant["status"] != "consumed" or grant["consumed_action_id"] != action_id or datetime.fromisoformat(grant["expires_at"]) <= datetime.now(UTC):
                    raise Failure("PERMISSION_DENIED", "发送前授权已失效", 403)
            for source in request["sources"]:
                if version(self.engine.resources.resolve(source["resource_id"], task_id=deps.task_id)) != source["version"]:
                    raise Failure("VERSION_CONFLICT", "发送前输入版本变化", 409)
        response = self.http.get(request["url"], guard=guard, allowed_origins=request["destinations"] if admission or kind == "web.search" else None)
        guard(request["url"])
        body = response.pop("body")
        sha = hashlib.sha256(body).hexdigest()
        staging = self.engine.resources.task_root(deps.task_id) / "web" / action_id / "source.bin"
        native_path(staging.parent).mkdir(parents=True, exist_ok=True)
        native_path(staging).write_bytes(body)
        raw = self.engine.resources.import_file(deps.task_id, action_id, staging, f"web/{action_id}/source.bin")
        citation = {"url": response["url"], "source_sha256": sha, "observed_at": response["observed_at"], "snapshot_artifact_id": raw["artifact_id"]}
        text = decode_text(body, response["headers"].get("content-type", ""))
        if kind == "web.search":
            provider = state["config_snapshot"]["search"]["provider"]
            results = search_results(provider, text)
            return {"citation": citation, "page": page, "has_more": None,
                "next_page": page+1 if provider in {"baidu_html", "searxng"} and page < 10 else None,
                "hops": response["hops"], "untrusted_content": True, "results": results}
        media_type = response["headers"].get("content-type", "").split(";")[0].strip().lower()
        if media_type not in {"text/html", "text/plain", "application/json", "text/markdown", "application/xhtml+xml"}:
            raise Failure("PRECONDITION_MISSING", "当前网页读取仅支持HTML、纯文本、Markdown和JSON；不把二进制当文本", 422)
        extracted = TextPage.extract(text) if "html" in media_type else text
        page = self.engine.resources.write(deps.task_id, action_id, f"web/{uid()}/page.txt", extracted)
        window = utf8_window(extracted.encode("utf-8"))
        return {"citation": citation, "text_artifact_id": page["artifact_id"],
            "hops": response["hops"], "untrusted_content": True,
            "continuation": "使用read_artifact读取text_artifact_id，offset=next继续；原文完整保留。",
            **window}
