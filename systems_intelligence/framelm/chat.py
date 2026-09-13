"""Persistent, context-grounded conversations for Context Studio."""
from datetime import datetime, timezone
import json
import re
import uuid
from .core import Pipeline, frame_prompt
from .context_store import ConflictError
from .controller.hooks import observed, emit, outbox


class ChatService:
    def __init__(self, store, config):
        self.store, self.config = store, config
        with store.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS chat_sessions (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, revision INTEGER NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS chat_turns (
                session_id TEXT NOT NULL, number INTEGER NOT NULL, request_id TEXT NOT NULL,
                prompt TEXT NOT NULL, topic TEXT NOT NULL, result TEXT NOT NULL,
                created_at TEXT NOT NULL, PRIMARY KEY(session_id,number),
                UNIQUE(session_id,request_id))""")

    @observed('chat.create', lambda self, *a, **kw: self.config)
    def create(self):
        now = datetime.now(timezone.utc).isoformat()
        session_id = str(uuid.uuid4())
        with self.store.connect() as db:
            db.execute("INSERT INTO chat_sessions VALUES (?,?,0,?,?)", (session_id,"New conversation",now,now))
            outbox(db, 'chat.created', 'chat', session_id, dict(db.execute('SELECT * FROM chat_sessions WHERE id=?',(session_id,)).fetchone()))
        return self.get(session_id)

    @observed('chat.list', lambda self, *a, **kw: self.config)
    def list(self):
        with self.store.connect() as db:
            return {"sessions": [dict(r) for r in db.execute("SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT 100")],
                    "backend": self.config["backend"], "model": self.config.get("model", ""),
                    "list_limit": 100}

    @observed('chat.get', lambda self, *a, **kw: self.config)
    def get(self, session_id):
        with self.store.connect() as db:
            row = db.execute("SELECT * FROM chat_sessions WHERE id=?", (session_id,)).fetchone()
            if row is None:
                raise ValueError("Conversation does not exist.")
            turns = db.execute("SELECT * FROM chat_turns WHERE session_id=? ORDER BY number DESC LIMIT 100", (session_id,)).fetchall()
            return dict(row) | {"turns": [dict(t) | {"result": json.loads(t["result"])} for t in reversed(turns)], "display_limit":100}

    @staticmethod
    def resolve_followup(prompt, previous_topic, budget):
        # Explicit, inspectable topic carryover; no old answers or retrieved text
        # are supplied as evidence for a new turn.
        followup = bool(re.search(r"\b(it|its|they|them|their|those|that|this)\b", prompt, re.I)
                        or re.fullmatch(r"\s*(?:please\s+)?(?:tell me more|go on|continue|elaborate|(?:give |show )?(?:me )?(?:an )?examples?)[.!?\s]*", prompt, re.I))
        if previous_topic and followup:
            prefix, middle = "Conversation topic: ", "\nCurrent request: "
            remaining = budget - len(prefix) - len(middle) - len(prompt)
            if remaining >= 30:
                topic = previous_topic[:min(2000, remaining)]
                return prefix + topic + middle + prompt, previous_topic, True
        return prompt, prompt, False

    @observed('chat.send', lambda self, *a, **kw: self.config)
    def send(self, session_id, prompt, revision, request_id):
        frame_prompt(prompt, self.config["max_prompt_chars"])
        if type(revision) is not int or revision < 0:
            raise ValueError("Conversation revision must be a non-negative integer.")
        if not isinstance(request_id,str) or not re.fullmatch(r"[a-zA-Z0-9-]{8,80}",request_id):
            raise ValueError("A valid request ID is required.")
        with self.store.connect() as db:
            old = db.execute("SELECT prompt FROM chat_turns WHERE session_id=? AND request_id=?", (session_id,request_id)).fetchone()
            if old:
                if old["prompt"] != prompt:
                    raise ConflictError("Request ID already belongs to another message.")
                return self.get(session_id)
        session = self.get(session_id)
        if session["revision"] != revision:
            raise ConflictError("Conversation changed in another window. Reopen it before sending.")
        topic = session["turns"][-1]["topic"] if session["turns"] else ""
        effective, topic, carried = self.resolve_followup(prompt,topic,self.config["max_prompt_chars"])
        emit('chat.followup', {'effective_prompt':effective,'topic':topic,'carried':carried}, entities=['chat:'+session_id])
        result = Pipeline(self.config).run(effective, retrieval_query=topic if carried else None)
        result["conversation"] = {"used_previous_topic":carried, "retrieval_prompt":effective}
        now = datetime.now(timezone.utc).isoformat()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            title = prompt.strip()[:70] if revision == 0 else session["title"]
            cursor = db.execute("UPDATE chat_sessions SET revision=revision+1,title=?,updated_at=? WHERE id=? AND revision=?", (title,now,session_id,revision))
            if not cursor.rowcount:
                # A concurrent identical retry can reuse the committed result.
                old = db.execute("SELECT prompt FROM chat_turns WHERE session_id=? AND request_id=?",(session_id,request_id)).fetchone()
                if not old or old["prompt"] != prompt:
                    raise ConflictError("Another message completed first. Reopen the conversation.")
            else:
                db.execute("INSERT INTO chat_turns VALUES (?,?,?,?,?,?,?)", (session_id,revision+1,request_id,prompt,topic,json.dumps(result,ensure_ascii=False),now))
                outbox(db, 'chat.updated', 'chat', session_id, dict(db.execute('SELECT * FROM chat_sessions WHERE id=?',(session_id,)).fetchone()))
                outbox(db, 'chat.turn', 'turn', f'{session_id}:{revision+1}',
                       {'session_id':session_id,'number':revision+1,'request_id':request_id,'prompt':prompt,'topic':topic,'result':result,'created_at':now})
        return self.get(session_id)
