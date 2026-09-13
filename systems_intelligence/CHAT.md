# FrameLM Chat — version 0.3

Context Studio now includes a chatbot layer. Launch it with:

```powershell
python -m framelm studio
```

Or double-click `start-context-studio.cmd`. The Chat page opens first. No new
packages are needed for the default local extractive backend.

## Upgrading an existing copy

Stop the running Studio server before updating. Replace the application files while
retaining your existing `state/` folder and custom configuration files. The new
chat tables are added automatically without deleting contexts. Restart Studio and
refresh the browser to load the new Chat tab.

## Conversation workflow

1. Enable the contexts you want the chatbot to reference in Contexts.
2. Open Chat and send a question. Ctrl+Enter also sends the message.
3. Read the reply and expand References to inspect the evidence used for it.
4. Ask a follow-up or start a new conversation. Earlier conversations remain in
   the left-hand list and survive server restarts.
5. Open context from a reply's source card to edit or disable its source.
6. Export chat downloads the currently displayed conversation as JSON.

Each turn passes through FrameLM's existing prompt → frame → context → resolution
→ response pipeline. It searches the current index, so edits and disabled states
apply to subsequent turns. Missing evidence produces an insufficient-context
response. Source identifiers are local to each reply.

Replies retain evidence snapshots for auditability. Disabling a source does not
rewrite or erase old replies or their reference snapshots. Those old replies are
not fed back into retrieval or sent to the model as factual evidence.

## Backends

The chatbot uses the same configuration selected when launching Studio.

- Default `configs/demo.json`: local extractive replies assembled from relevant
  source sentences. Works immediately; this mode is not generative conversation.
- `configs/served.json`: generated candidates from the configured Ollama model.
  Install a model in your Ollama server and replace its placeholder model name.
- `configs/tiny.json`: the optional custom Transformer, requiring PyTorch and
  trained weights as described in README.md.

For generated chatbot responses:

```powershell
python -m framelm --config configs/served.json studio
```

The chatbot passes the framed request and retrieved evidence to the configured
model endpoint. It does not automatically download models. The page identifies
the requested backend, and every reply identifies its selected backend and any
fallback warning. The existing candidate ranker can prefer an extractive fallback
over a generated candidate. To require generated candidates, set
`include_extractive_fallback` to `false`; model failures then remain errors, and
candidates lacking valid citation IDs may produce an unresolved response.

The separate Retrieval check tab continues to use only the local extractive mode.

## Follow-ups and memory

Chat history is persistent. Topic carryover is intentionally lightweight: phrases
such as “tell me more,” “continue,” or pronouns such as “it” and “that” cause the
most recent topic to be included with the current request. Retrieval uses that
prior topic, while the model sees both the topic and the new request. New explicit
questions replace the topic. Expand Topic used for this follow-up to inspect the
actual framed prompt.

This is a heuristic, not full conversational understanding. It does not resolve
multiple competing referents, search all previous turns, or use complete chat
history as a model prompt. A specific standalone question gives better control
when a follow-up changes subject or introduces a new subtopic. The default
extractive backend may repeat the same excerpts on follow-up requests; a trained
model is needed for flexible synthesis. FrameLM's lexical ranking and citation-ID
checks do not guarantee factual correctness.

The conversation list shows the most recent 100 sessions. Each conversation shows
its latest 100 turns; older turns remain in the database. Chat export covers those
displayed turns. There is no delete-chat action or full-history export UI in this
version.

## Persistence and request handling

Conversations and evidence snapshots are stored in `chat_sessions` and `chat_turns`
in the configured SQLite database alongside managed contexts. Use the existing
Context Studio database backup procedure to preserve them. Rebuilding the context
index preserves conversations.

Each successful turn commits its user prompt and answer together. A request ID
makes retrying the same completed request idempotent in storage. Revision checks
reject conflicting sends from another window. Failed requests retain the message
in the browser composer and do not save a partial turn. No context is created
from a chatbot reply automatically.

## Verification

The full suite reports 48 tests passed and 4 optional PyTorch tests skipped. New
checks cover source-grounded responses, simple follow-ups, disabled-source
exclusion on subsequent turns, topic changes, restart persistence, isolated
sessions, retry handling, stale revisions, backend failures, configured backend
selection, and the chat HTTP endpoints. JavaScript passes syntax checking.

Visual browser interaction remains unverified: the remote test browser could not
reach this environment's local server during the prior Context Studio check.
No live Ollama model or custom neural weights were available for inference tests.
