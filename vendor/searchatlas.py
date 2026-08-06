"""
SearchAtlas API client (dependency-free, stdlib only).

Auth: every request sends the `X-API-Key` header.
Each SearchAtlas product lives on its own subdomain (see SERVICES below);
all share the same API key.

Usage:
    from searchatlas import SearchAtlas
    sa = SearchAtlas()                       # reads key from env or .env
    projects = sa.list_projects()            # -> list of all projects (auto-paginated)
    one = sa.get("core", "/api/customer/projects/projects/186316/")

Docs: https://docs.searchatlas.com/
"""

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

# --- Service base URLs (verified from docs.searchatlas.com, 2026-07) ---
SERVICES = {
    "core":       "https://api.searchatlas.com",          # accounts, projects, quota, indexing
    "content":    "https://ca.searchatlas.com",           # Content Assistant / AI writer
    "cms":        "https://ucmsi.searchatlas.com",         # Universal CMS publishing
    "gsc":        "https://gsc.searchatlas.com",           # Google Search Console reports
    "keyword":    "https://keyword.searchatlas.com",       # Keyword Explorer / rank tracker
    "backlink":   "https://backlink.searchatlas.com",      # Backlink analysis
    "llmvis":     "https://llmvis.searchatlas.com",        # LLM Visibility
    "otto_ppc":   "https://otto-ppc.searchatlas.com",      # OTTO PPC (Google/Facebook ads)
    "site":       "https://sa.searchatlas.com",            # Site Auditor / OTTO / GBP / Social
    "builder":    "https://api.builder.searchatlas.com",   # Website Builder
    "agent":      "https://agent.searchatlas.com",         # AI Agent (Atlas brains)
    "mcp":        "https://mcp.searchatlas.com",           # MCP server
}

DEFAULT_TIMEOUT = 60
MAX_ATTEMPTS = 6
RETRY_STATUSES = {429, 500, 502, 503, 504}


def _load_key():
    """Return API key from env var, falling back to a sibling .env file."""
    key = os.environ.get("SEARCHATLAS_API_KEY")
    if key:
        return key.strip()
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("SEARCHATLAS_API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise RuntimeError(
        "No API key found. Set SEARCHATLAS_API_KEY or add it to searchatlas/.env"
    )


class SearchAtlasError(Exception):
    def __init__(self, status, url, body):
        self.status = status
        self.url = url
        self.body = body
        super().__init__(f"HTTP {status} for {url}: {body[:500]}")


class SearchAtlas:
    def __init__(self, api_key=None, timeout=DEFAULT_TIMEOUT):
        self.api_key = api_key or _load_key()
        self.timeout = timeout

    # --- low-level request ---
    def request(self, method, service, path, params=None, json_body=None):
        if service not in SERVICES:
            raise ValueError(f"Unknown service '{service}'. Options: {list(SERVICES)}")
        base = SERVICES[service]
        url = base.rstrip("/") + "/" + path.lstrip("/")
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)

        data = None
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            # Cloudflare (error 1010) bans the default Python-urllib UA; send a normal one.
            "User-Agent": "gsm-growth-searchatlas-client/1.0",
        }
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())

        # Retry on rate limits and transient 5xx, honouring Retry-After when given.
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read().decode("utf-8")
                break
            except urllib.error.HTTPError as e:
                if e.code not in RETRY_STATUSES or attempt == MAX_ATTEMPTS:
                    raise SearchAtlasError(e.code, url, e.read().decode("utf-8", "replace"))
                retry_after = e.headers.get("Retry-After") if e.headers else None
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = 2.0 ** (attempt - 1)
                time.sleep(min(delay, 60))
            except urllib.error.URLError:
                if attempt == MAX_ATTEMPTS:
                    raise
                time.sleep(min(2.0 ** (attempt - 1), 60))

        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return body

    def get(self, service, path, params=None):
        return self.request("GET", service, path, params=params)

    def post(self, service, path, json_body=None, params=None):
        return self.request("POST", service, path, params=params, json_body=json_body)

    def patch(self, service, path, json_body=None, params=None):
        return self.request("PATCH", service, path, params=params, json_body=json_body)

    def delete(self, service, path, params=None):
        return self.request("DELETE", service, path, params=params)

    def post_multipart(self, service, path, field_name, filename, content, content_type="text/csv"):
        """Minimal multipart/form-data POST for endpoints that take a file upload."""
        if service not in SERVICES:
            raise ValueError(f"Unknown service '{service}'. Options: {list(SERVICES)}")
        url = SERVICES[service].rstrip("/") + "/" + path.lstrip("/")
        if isinstance(content, str):
            content = content.encode("utf-8")

        boundary = "----searchatlasclient" + os.urandom(8).hex()
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode(),
            f"Content-Type: {content_type}\r\n\r\n".encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        headers = {
            "X-API-Key": self.api_key,
            "Accept": "application/json",
            "User-Agent": "gsm-growth-searchatlas-client/1.0",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raise SearchAtlasError(e.code, url, e.read().decode("utf-8", "replace"))
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return raw

    def paginate(self, service, path, params=None):
        """Yield every item from a DRF-style {count,next,previous,results} endpoint."""
        page = self.get(service, path, params=params)
        while page:
            if isinstance(page, dict) and "results" in page:
                for item in page["results"]:
                    yield item
                nxt = page.get("next")
                if not nxt:
                    break
                # `next` is a full URL; strip base to reuse request()
                path = urllib.parse.urlparse(nxt).path
                query = urllib.parse.urlparse(nxt).query
                params = dict(urllib.parse.parse_qsl(query)) if query else None
                page = self.get(service, path, params=params)
            else:
                # non-paginated response
                if isinstance(page, list):
                    yield from page
                break

    # --- convenience helpers (verified read endpoints) ---
    def list_projects(self):
        """All customer projects (core service)."""
        return list(self.paginate("core", "/api/customer/projects/projects/"))

    def get_project(self, project_id):
        return self.get("core", f"/api/customer/projects/projects/{project_id}/")

    def project_status(self, project_id):
        return self.get("core", f"/api/customer/projects/projects/{project_id}/status/")

    def whoami(self):
        """Quick connectivity check: returns project count."""
        page = self.get("core", "/api/customer/projects/projects/")
        return {"connected": True, "project_count": page.get("count") if isinstance(page, dict) else None}


if __name__ == "__main__":
    sa = SearchAtlas()
    info = sa.whoami()
    print(json.dumps(info, indent=2))
    for p in sa.list_projects():
        print(f"{p['id']:>8}  {p.get('status',''):10}  {p['domain_url']}")
