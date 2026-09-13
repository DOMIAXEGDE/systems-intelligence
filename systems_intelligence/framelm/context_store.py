"""Transactional context catalogue and live FrameLM full-text index."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid
from .controller.hooks import observed, emit, initialize_outbox, outbox


class ConflictError(ValueError):
    pass


class Cancelled(RuntimeError):
    pass


def initialize(db):
    initialize_outbox(db)
    db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(source UNINDEXED, text, tokenize='unicode61')")
    db.execute("""CREATE TABLE IF NOT EXISTS managed_contexts (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), tags TEXT NOT NULL,
        provenance TEXT NOT NULL, generation_key TEXT UNIQUE, source_path TEXT UNIQUE,
        revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    db.execute("CREATE TABLE IF NOT EXISTS managed_chunks (chunk_id INTEGER PRIMARY KEY, context_id TEXT NOT NULL)")
    db.execute("CREATE INDEX IF NOT EXISTS managed_chunks_context ON managed_chunks(context_id)")


def add_chunks(db, source, body, context_id=None, chunk_words=180, overlap=30):
    words = body.split()
    count = 0
    for start in range(0, len(words), chunk_words - overlap):
        chunk = " ".join(words[start:start + chunk_words])
        cursor = db.execute("INSERT INTO chunks(source,text) VALUES (?,?)", (source, chunk))
        if context_id:
            db.execute("INSERT INTO managed_chunks VALUES (?,?)", (cursor.lastrowid, context_id))
        count += 1
        if start + chunk_words >= len(words):
            break
    return count


def index_context(db, item):
    ids = [r[0] for r in db.execute("SELECT chunk_id FROM managed_chunks WHERE context_id=?", (item["id"],))]
    db.executemany("DELETE FROM chunks WHERE rowid=?", [(i,) for i in ids])
    db.execute("DELETE FROM managed_chunks WHERE context_id=?", (item["id"],))
    if item["source_path"]:
        # Once adopted, a source file is overridden by its managed record, even
        # when that record is disabled. Old raw-file chunks must disappear too.
        db.execute("DELETE FROM chunks WHERE source=?", (item["source_path"],))
    if item["enabled"]:
        return add_chunks(db, f"context:{item['id']} / {item['title']}",
                          item["title"] + "\n" + item["body"], item["id"])
    return 0


def unpack(row):
    item = dict(row)
    item["enabled"] = bool(item["enabled"])
    item["provenance"] = json.loads(item["provenance"])
    return item


def index_change(db, context_id):
    return {'kind':'index_part','id':context_id,'state':{'chunks':[dict(r) for r in db.execute(
        'SELECT c.rowid,c.source,c.text FROM chunks c JOIN managed_chunks m ON m.chunk_id=c.rowid WHERE m.context_id=? ORDER BY c.rowid',(context_id,))]}}


class ContextStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            initialize(db)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def validate(item):
        for name, cap in (("title", 1000), ("body", 1000000), ("tags", 4096)):
            if not isinstance(item.get(name, ""), str) or len(item.get(name, "").encode("utf-8")) > cap:
                raise ValueError(f"{name} must be text of at most {cap:,} UTF-8 bytes.")
        if not item.get("title", "").strip() or not item.get("body", "").strip():
            raise ValueError("Title and context text cannot be empty.")
        if type(item.get("enabled", True)) is not bool:
            raise ValueError("enabled must be boolean.")

    def _insert(self, db, item):
        self.validate(item)
        now = datetime.now(timezone.utc).isoformat()
        context_id = str(uuid.uuid4())
        cursor = db.execute("""INSERT INTO managed_contexts
            VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING""",
            (context_id, item["title"], item["body"], int(item.get("enabled", True)), item.get("tags", ""),
             json.dumps(item.get("provenance", {"kind": "manual"}), ensure_ascii=False),
             item.get("generation_key"), item.get("source_path"), 1, now, now))
        if cursor.rowcount:
            row = db.execute("SELECT * FROM managed_contexts WHERE id=?", (context_id,)).fetchone()
            index_context(db, row)
            outbox(db, 'context.created', 'context', context_id, unpack(row),extra_changes=[index_change(db,context_id)])
            return context_id
        return None

    @observed('context.append', lambda self, *a, **kw: self.path)
    def append(self, item):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            context_id = self._insert(db, item)
            if context_id is None:
                raise ConflictError("This source or generated ordinal already exists.")
            return unpack(db.execute("SELECT * FROM managed_contexts WHERE id=?", (context_id,)).fetchone())

    @observed('context.get', lambda self, *a, **kw: self.path)
    def get(self, context_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM managed_contexts WHERE id=?", (context_id,)).fetchone()
            if row is None:
                raise ValueError("Context does not exist.")
            return unpack(row)

    @observed('context.edit', lambda self, *a, **kw: self.path)
    def edit(self, context_id, revision, item):
        self.validate(item)
        if type(revision) is not int:
            raise ValueError("An integer revision is required.")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""UPDATE managed_contexts SET title=?,body=?,enabled=?,tags=?,
                revision=revision+1,updated_at=? WHERE id=? AND revision=?""",
                (item["title"], item["body"], int(item.get("enabled", True)), item.get("tags", ""),
                 datetime.now(timezone.utc).isoformat(), context_id, revision))
            if not cursor.rowcount:
                raise ConflictError("Context changed in another window. Reload it before saving.")
            row = db.execute("SELECT * FROM managed_contexts WHERE id=?", (context_id,)).fetchone()
            index_context(db, row)
            outbox(db, 'context.updated', 'context', context_id, unpack(row),extra_changes=[index_change(db,context_id)])
            return unpack(row)

    @observed('context.toggle', lambda self, *a, **kw: self.path)
    def toggle(self, context_id, revision, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean.")
        item = self.get(context_id)
        item["enabled"] = enabled
        return self.edit(context_id, revision, item)

    @observed('context.list', lambda self, *a, **kw: self.path)
    def list(self, query="", status="all", page=0, page_size=40):
        if status not in {"all", "enabled", "disabled"} or page < 0 or not 1 <= page_size <= 100:
            raise ValueError("Invalid list filter or page.")
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        where = "(title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' OR tags LIKE ? ESCAPE '\\')"
        args = [pattern] * 3
        if status != "all":
            where += " AND enabled=?"
            args.append(int(status == "enabled"))
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM managed_contexts WHERE " + where, args).fetchone()[0]
            # List previews never return full document bodies/provenance.
            rows = db.execute("SELECT id,title,enabled,tags,revision,substr(body,1,160) AS preview FROM managed_contexts WHERE " + where + " ORDER BY created_at DESC,id LIMIT ? OFFSET ?", args + [page_size, page * page_size]).fetchall()
            stats = dict(db.execute("SELECT count(*) AS total,coalesce(sum(enabled),0) AS enabled FROM managed_contexts").fetchone())
            stats["disabled"] = stats["total"] - stats["enabled"]
            return {"items": [dict(r) | {"enabled": bool(r["enabled"])} for r in rows], "total": total, "page": page, "page_size": page_size, "stats": stats}

    @observed('generator.generate', lambda self, *a, **kw: self.path)
    def append_generated(self, recipe, cancel=None, progress=None):
        added, skipped = 0, 0
        batch_id = str(uuid.uuid4())
        job={'id':batch_id,'recipe':recipe.__dict__,'state':'running','processed':0,'added':0,'skipped':0}
        emit('generator.batch', job, entities=['generator:'+batch_id], phase='started',changes=[{'kind':'generator','id':batch_id,'state':job}])
        try:
            with self.connect() as db:
                db.execute("BEGIN IMMEDIATE")
                for number, item in enumerate(recipe.records(), 1):
                    if cancel and cancel.is_set():
                        raise Cancelled("Generation cancelled; the entire batch was rolled back.")
                    item["provenance"]["batch_id"] = batch_id
                    inserted = self._insert(db, item)
                    if inserted:
                        added += 1
                    else:
                        skipped += 1
                    emit('generator.record', {'ordinal':number,'record':item,'status':'produced' if inserted else 'skipped'},
                         entities=['generator:'+batch_id] + (['context:'+inserted] if inserted else []), phase='provisional')
                    emit('generator.progress', {'processed':number,'added':added,'skipped':skipped}, entities=['generator:'+batch_id], phase='provisional',
                         changes=[{'kind':'generator','id':batch_id,'state':dict(job,processed=number,added=added,skipped=skipped)}])
                    if progress and (number % 100 == 0 or number == 1):
                        progress(number)
                if cancel and cancel.is_set():
                    raise Cancelled("Generation cancelled; the entire batch was rolled back.")
                outbox(db, 'generator.committed', 'generator', batch_id,
                       {'id':batch_id,'recipe':recipe.__dict__,'state':'complete','processed':added+skipped,'count':added+skipped,'added':added,'skipped':skipped})
        except Exception as exc:
            emit('generator.rollback', {'error':str(exc),'added_before_rollback':added,'skipped':skipped},
                 entities=['generator:'+batch_id], phase='cancelled' if isinstance(exc, Cancelled) else 'failed',
                 changes=[{'kind':'generator','id':batch_id,'state':{'id':batch_id,'recipe':recipe.__dict__,
                           'state':'cancelled' if isinstance(exc, Cancelled) else 'failed','processed':added+skipped,
                           'added':0,'skipped':skipped,'error':str(exc)}}])
            raise
        return {"added": added, "skipped": skipped, "batch_id": batch_id}

    @observed('context.adopt', lambda self, *a, **kw: self.path)
    def adopt_files(self, directory):
        directory = Path(directory).resolve()
        if not directory.is_dir():
            raise ValueError("Context directory does not exist.")
        added = 0
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            for path in sorted(directory.rglob("*")):
                if path.is_symlink() or not path.is_file() or path.suffix.lower() not in {".txt", ".md"}:
                    continue
                source = path.relative_to(directory).as_posix()
                if db.execute("SELECT 1 FROM managed_contexts WHERE source_path=?", (source,)).fetchone():
                    continue
                if path.stat().st_size > 1000000:
                    raise ValueError(f"{source} exceeds the 1 MB managed-document limit; split it into smaller files.")
                text = path.read_text(encoding="utf-8")
                if text.strip():
                    added += bool(self._insert(db, {"title": path.stem, "body": text, "enabled": True,
                                 "tags": "imported", "source_path": source,
                                 "provenance": {"kind": "file", "source": source}}))
        return added

    def export_jsonl(self):
        from .controller import get_controller
        from .controller.hooks import scope
        controller = get_controller({'index_path':str(self.path)})
        with scope(controller):
            controller.emit('context.export', phase='started',entities=['context:*'])
            try:
                with self.connect() as db:
                    for row in db.execute("SELECT * FROM managed_contexts ORDER BY created_at,id"):
                        yield json.dumps(unpack(row), ensure_ascii=False) + "\n"
            except (Exception, GeneratorExit) as exc:
                controller.emit('context.export',{'error':str(exc)},phase='cancelled' if isinstance(exc,GeneratorExit) else 'failed',entities=['context:*'])
                raise
            else: controller.emit('context.export',phase='completed',entities=['context:*'])
