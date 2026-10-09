#!/usr/bin/env python3
"""hfmock/mock_hub.py - a loopback HTTP stand-in for huggingface.co (DRY=1 and tests/ only), serving the REST shapes
hf_flow.py calls, over the same FAKEHUB_DIR storage the fake huggingface_hub package writes (hfmock/store.py).

  python3 mock_hub.py serve --dir FAKEHUB_DIR [--port 0] [--port-file F]   bind 127.0.0.1 only; --port 0 = a free port,
                                                                            written to --port-file once listening
  python3 mock_hub.py token --dir FAKEHUB_DIR --name USER [--role write|read] [--token-file F]
                                                                            register a MOCK token (hf_MOCK...), written
                                                                            to F as HF_TOKEN=... (0600) - never a real one
Endpoints: GET /api/whoami-v2 · GET /api/models/<o>/<n>[/revision/<rev>][?blobs=true] · POST /api/repos/create ·
DELETE /api/repos/delete · GET /<o>/<n>/resolve/<rev>/<path> · GET /healthz. Status codes as huggingface.co gave them
on 2026-10-07: anonymous + missing/private repo = 401 "Invalid username or password." (no X-Error-Code); authenticated +
missing = 404 with X-Error-Code: RepoNotFound; whoami-v2 without a token = 401.
Test knob (tests only): a file FAKEHUB_DIR/fail_delete holding "always" makes every DELETE answer 500, holding an
integer N makes the next N DELETEs answer 500 (counted down in the file) - a hub that refuses to delete a repo.
"""
import argparse
import json
import os
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from store import HubError, Store  # noqa: E402


class Handler(BaseHTTPRequestHandler):
    store = None
    server_version = "hfmock/1"

    def log_message(self, fmt, *args):          # one line per request on stderr, never a header (the token rides in one)
        sys.stderr.write("[mock_hub] %s %s\n" % (self.command, self.path.split("?")[0]))

    def _token(self):
        h = self.headers.get("Authorization", "")
        return h[7:].strip() if h.startswith("Bearer ") else None

    def _send(self, status, body=b"", ctype="application/json", headers=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _err(self, e):
        headers = {"X-Error-Code": e.code} if e.code else {}
        if e.status == 401:
            headers["WWW-Authenticate"] = 'Bearer realm="Authentication required", charset="UTF-8"'
        self._send(e.status, {"error": str(e)}, headers=dict(headers, **{"X-Error-Message": str(e)}))

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            raise HubError(400, "invalid JSON body")

    def do_GET(self):
        u = urlparse(self.path)
        parts = [unquote(p) for p in u.path.split("/") if p]
        q = parse_qs(u.query)
        try:
            if parts == ["healthz"]:
                return self._send(200, "ok", "text/plain")
            if parts == ["api", "whoami-v2"]:
                who = self.store.who(self._token())
                if who is None:
                    raise HubError(401, "Invalid credentials in Authorization header")
                return self._send(200, {"type": "user", "name": who[0], "fullname": who[0], "email": "hidden@example.invalid",
                                        "orgs": [], "auth": {"type": "access_token",
                                                             "accessToken": {"displayName": "hfmock", "role": who[1]}}})
            if len(parts) >= 4 and parts[0] == "api" and parts[1] == "models":
                repo = f"{parts[2]}/{parts[3]}"
                rev = None
                if len(parts) == 6 and parts[4] == "revision":
                    rev = parts[5]
                elif len(parts) != 4:
                    raise HubError(404, "not found")
                blobs = q.get("blobs", ["false"])[0].lower() in ("1", "true")
                return self._send(200, self.store.info(repo, self._token(), rev, blobs=blobs))
            if len(parts) >= 5 and parts[2] == "resolve":
                repo, rev, path = f"{parts[0]}/{parts[1]}", parts[3], "/".join(parts[4:])
                meta = self.store.visible(repo, self._token())
                rev = self.store.resolve_rev(meta, rev)
                p = self.store.file_path(repo, rev, path)
                self.store.event("resolve", repo, f"rev={rev} file={path}")
                with open(p, "rb") as fh:
                    return self._send(200, fh.read(), "application/octet-stream", {"X-Repo-Commit": rev})
            raise HubError(404, "not found")
        except HubError as e:
            self._err(e)

    def do_POST(self):
        try:
            if urlparse(self.path).path != "/api/repos/create":
                raise HubError(404, "not found")
            b = self._body()
            if b.get("type", "model") != "model":
                raise HubError(400, "hfmock: model repos only")
            org, name = b.get("organization"), b.get("name")
            who = self.store.who(self._token())
            if who is None:
                raise HubError(401, "Invalid username or password.")
            repo = f"{org or who[0]}/{name}"
            private = b.get("visibility") == "private" if "visibility" in b else bool(b.get("private", False))
            self.store.create(repo, self._token(), private=private)
            self._send(200, {"url": f"https://huggingface.co/{repo}", "name": repo})
        except HubError as e:
            self._err(e)

    def _delete_refused(self):
        """The fail_delete test knob (module docstring): True when this DELETE must answer 500."""
        p = os.path.join(self.store.root, "fail_delete")
        try:
            v = open(p).read().strip()
        except OSError:
            return False
        if v == "always":
            return True
        n = int(v or 0)
        if n <= 0:
            return False
        with open(p, "w") as fh:
            fh.write(str(n - 1))
        return True

    def do_DELETE(self):
        try:
            if urlparse(self.path).path != "/api/repos/delete":
                raise HubError(404, "not found")
            if self._delete_refused():
                raise HubError(500, "Internal Error - We're working hard to fix this as soon as possible! (hfmock fail_delete)")
            b = self._body()
            who = self.store.who(self._token())
            if who is None:
                raise HubError(401, "Invalid username or password.")
            repo = f"{b.get('organization') or who[0]}/{b.get('name')}"
            self.store.delete(repo, self._token())
            self._send(200, {})
        except HubError as e:
            self._err(e)


def cmd_serve(a):
    Handler.store = Store(a.dir)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    port = srv.server_address[1]
    if a.port_file:
        tmp = a.port_file + ".tmp"
        with open(tmp, "w") as fh:
            fh.write(f"{port}\n")
        os.replace(tmp, a.port_file)
    sys.stderr.write(f"[mock_hub] serving {a.dir} on 127.0.0.1:{port}\n")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        t.join()
    except KeyboardInterrupt:
        pass


def cmd_token(a):
    tok = "hf_MOCK" + secrets.token_hex(16)            # visibly fake: hf_MOCK + 32 hex = 39 chars, matches hf_[A-Za-z0-9]{20,}
    Store(a.dir).add_token(tok, a.name, a.role)
    if a.token_file:
        old = os.umask(0o077)
        try:
            with open(a.token_file, "w") as fh:
                fh.write(f"HF_TOKEN={tok}\n")
        finally:
            os.umask(old)
        os.chmod(a.token_file, 0o600)
    print(f"[mock_hub] mock token registered for account {a.name} (role {a.role})"
          + (f" -> {a.token_file}" if a.token_file else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--dir", required=True)
    s.add_argument("--port", type=int, default=0)
    s.add_argument("--port-file")
    s.set_defaults(fn=cmd_serve)
    s = sub.add_parser("token")
    s.add_argument("--dir", required=True)
    s.add_argument("--name", required=True)
    s.add_argument("--role", default="write", choices=("write", "read"))
    s.add_argument("--token-file")
    s.set_defaults(fn=cmd_token)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
