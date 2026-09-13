"""Local-only Context Studio HTTP service. Python standard library only."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import sqlite3
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .core import Pipeline, SQLiteRetriever
from .context_store import ContextStore, ConflictError, Cancelled
from .generator import Recipe
from .chat import ChatService

WEB_ROOT = Path(__file__).parent / "studio_web"


class StudioApp:
    def __init__(self, config):
        self.config = config
        self.store = ContextStore(config["index_path"])
        self.store.adopt_files(config["context_dir"])
        self.chat = ChatService(self.store, config)
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.Lock()
        self.cancel = threading.Event()
        self.job = {"state": "idle", "processed": 0, "count": 0}
        self.worker = None

    def start_generation(self, recipe):
        plan = recipe.plan()
        with self.lock:
            if self.job["state"] == "running":
                raise ConflictError("A generation batch is already running.")
            self.cancel.clear()
            self.job = {"state": "running", "processed": 0, "count": plan["count"]}

        def progress(count):
            with self.lock:
                self.job["processed"] = count

        def work():
            try:
                result = self.store.append_generated(recipe, self.cancel, progress)
                state = {"state": "complete", "processed": plan["count"], "count": plan["count"], **result}
            except Cancelled as exc:
                state = {"state": "cancelled", "message": str(exc)}
            except Exception as exc:
                state = {"state": "failed", "message": str(exc)}
            with self.lock:
                self.job = state

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()
        return {"state": "running", "count": plan["count"]}


def make_server(config, port=8765):
    app = StudioApp(config)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def valid_host(self):
            return self.headers.get("Host") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def send_bytes(self, body, mime="application/json; charset=utf-8", status=200):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def respond(self, value, status=200):
            self.send_bytes(json.dumps(value, ensure_ascii=False).encode("utf-8"), status=status)

        def do_GET(self):
            if not self.valid_host():
                return self.respond({"error": "Local host required."}, 403)
            url = urlsplit(self.path)
            query = parse_qs(url.query)
            try:
                if url.path == "/":
                    html = (WEB_ROOT / "index.html").read_text(encoding="utf-8").replace("__CSRF_TOKEN__", app.token)
                    return self.send_bytes(html.encode("utf-8"), "text/html; charset=utf-8")
                if url.path in {"/app.js", "/style.css"}:
                    return self.send_bytes((WEB_ROOT / url.path[1:]).read_bytes(),
                                           "text/javascript; charset=utf-8" if url.path.endswith(".js") else "text/css; charset=utf-8")
                if url.path == "/api/contexts":
                    return self.respond(app.store.list(query.get("q", [""])[0], query.get("status", ["all"])[0], int(query.get("page", [0])[0])))
                if url.path == "/api/chats":
                    return self.respond(app.chat.list())
                if url.path == "/api/chat":
                    return self.respond(app.chat.get(query.get("id", [""])[0]))
                if url.path == "/api/context":
                    return self.respond(app.store.get(query.get("id", [""])[0]))
                if url.path == "/api/job":
                    with app.lock:
                        state = dict(app.job)
                    return self.respond(state)
                if url.path == "/api/export":
                    self.send_response(200)
                    self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
                    self.send_header("Content-Disposition", 'attachment; filename="framelm-contexts.jsonl"')
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    for line in app.store.export_jsonl():
                        self.wfile.write(line.encode("utf-8"))
                    return
                return self.respond({"error": "Not found."}, 404)
            except (ValueError, OSError, sqlite3.Error) as exc:
                return self.respond({"error": str(exc)}, 400)

        def do_POST(self):
            if not self.valid_host() or not secrets.compare_digest(self.headers.get("X-Studio-Token", ""), app.token):
                return self.respond({"error": "Reload Context Studio to use this session."}, 403)
            origin = self.headers.get("Origin")
            if origin and origin not in {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}:
                return self.respond({"error": "Cross-origin requests are not accepted."}, 403)
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 2000000:
                    raise ValueError("Request body must be between 1 byte and 2 MB.")
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError("Request must be a JSON object.")
                if self.path == "/api/append":
                    # Browser users may edit content, not database provenance/IDs.
                    item = {k: data[k] for k in ("title", "body", "enabled", "tags") if k in data}
                    return self.respond(app.store.append(item))
                if self.path == "/api/chat/new":
                    return self.respond(app.chat.create())
                if self.path == "/api/chat/send":
                    return self.respond(app.chat.send(data["id"], data["prompt"], data["revision"], data["request_id"]))
                if self.path == "/api/edit":
                    return self.respond(app.store.edit(data["id"], data["revision"], data))
                if self.path == "/api/toggle":
                    return self.respond(app.store.toggle(data["id"], data["revision"], data["enabled"]))
                if self.path == "/api/preview":
                    return self.respond(Recipe.from_dict(data).preview())
                if self.path == "/api/generate":
                    return self.respond(app.start_generation(Recipe.from_dict(data)))
                if self.path == "/api/cancel":
                    app.cancel.set()
                    return self.respond({"message": "Cancellation requested."})
                if self.path == "/api/adopt":
                    return self.respond({"added": app.store.adopt_files(app.config["context_dir"])})
                if self.path == "/api/reindex":
                    return self.respond({"chunks": SQLiteRetriever(Path(app.config["index_path"])).build(Path(app.config["context_dir"]))})
                if self.path == "/api/ask":
                    # Studio's retrieval check always stays local and extractive.
                    return self.respond(Pipeline(app.config | {"backend": "extractive"}).run(data["prompt"]))
                return self.respond({"error": "Not found."}, 404)
            except ConflictError as exc:
                return self.respond({"error": str(exc)}, 409)
            except (ValueError, KeyError, TypeError, OSError, RuntimeError, sqlite3.Error) as exc:
                return self.respond({"error": str(exc)}, 400)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.app = app
    return server


def serve(config, port=8765, open_browser=True):
    server = make_server(config, port)
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"FrameLM Context Studio: {url}\nKeep this terminal open. Press Ctrl+C to stop.", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nClosing Context Studio.")
    finally:
        server.app.cancel.set()
        if server.app.worker:
            server.app.worker.join(timeout=5)
        server.server_close()
