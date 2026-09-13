# systems-intelligence (FrameLM)

**Version 0.4 adds the P&R Controller:** shared commands and events, recoverable
state plots, historical replay, GIF/data export, isolated branches, and approved
Python skills with scoped autonomy. Start with [CONTROLLER.md](CONTROLLER.md) and
run `build.ps1` to install and test both packages using Python 3.11+.

See `CHAT.md` for the chatbot, saved conversations, sources, and model-backend setup. Open `CONTEXT_STUDIO.md` for the
source-flow mapping, GUI instructions, persistence rules, and generation examples.
To launch the local GUI, double-click `start-context-studio.cmd` on Windows or run:

```powershell
python -m framelm studio
```

It opens a local browser form for appending, editing, disabling/re-enabling, and
generating contexts. The controller shares P&R's Python dependencies; no C++
compilation, PyTorch, or model server is required for Context Studio.

A scalable language-system prototype implementing the requested architecture:

1. Receive a prompt.
2. Frame the prompt.
3. Reference related contexts.
4. Resolve the frame using those contexts.
5. Output the best-fit response candidate.

**Delivery status:** the local context pipeline and P&R controller are runnable.
The package also contains a trainable causal byte Transformer and an Ollama
adapter for existing small or large models. No trained neural weights are
included. The supplied examples are a training demonstration, not enough data
for a useful general-purpose language model.

## Run immediately on Windows / PowerShell

Install Python 3.11 or later if needed. Open PowerShell in `systems_intelligence`
and run `build.ps1`, then use the configured virtual environment:

```powershell
& ..\PandR_v3\.venv\Scripts\python.exe -m framelm index
& ..\PandR_v3\.venv\Scripts\python.exe -m framelm ask "What is an abelian group?"
& ..\PandR_v3\.venv\Scripts\python.exe -m framelm ask "How does context retrieval work?" --json
```

Use `py` instead of `python` if that is your Python launcher. These same commands
work in a Linux or macOS terminal. The default backend needs no model packages and
does not contact a server. It returns labeled excerpts, rather than generated
prose. This lets you inspect all five stages before introducing a neural model.

Add your own UTF-8 `.txt` or `.md` files to `contexts/`, then run the index command
again. Reindexing replaces the old index in a transaction and removes deleted
documents. SQLite must have FTS5 enabled. PDF/Word parsing is not included.

The JSON output exposes the frame, selected context excerpts and sources,
retrieval coverage, response candidates, scores, selected backend, and warnings.
It does not reveal hidden model reasoning. Citation IDs are local to each request;
document chunk IDs can change after a rebuild.

## Exact mapping to your architecture

| Stage | Implementation | Output |
|---|---|---|
| Receive prompt | Validate nonempty text and input length | Original request |
| Frame prompt | Rule-based intent, keywords and output-format detection | Structured `Frame` |
| Reference contexts | SQLite FTS5 / BM25 search, top-k and character budget | Context records with sources |
| Resolve frame | Extractive baseline, custom Transformer, or served model | One or more candidates |
| Output best fit | Validate citation IDs, rank candidates, return the highest score | Answer plus inspectable evidence |

The original prompt stays in the frame. The initial framer is deliberately small:
it does not fully parse constraints, handle conversational memory, infer unstated
intent, or recognize every output format. Its heuristics are English-oriented.
Unicode input is accepted, but lexical retrieval is not multilingual semantic
search. Replace `frame_prompt` for richer framing.

## What the model learns

The neural component estimates the next byte conditioned on the frame, selected
contexts, and previously generated response bytes:

`P(response | frame, contexts) = product_t P(byte_t | frame, contexts, byte_<t)`

Its architecture is token embeddings + learned positions + causally masked
self-attention/MLP blocks + layer normalization + a tied output projection.
The vocabulary has 256 UTF-8 bytes plus padding, beginning, and end tokens.
Training minimizes response-only cross-entropy; prompt positions and batch
padding do not contribute to the loss. Training and inference share the same
message serialization. Byte output can still contain invalid UTF-8 sequences;
decoding uses replacement characters when that happens.

The framer, retriever, and ranker are separate from those trainable weights.
Thus your five stages describe the overall system, while the Transformer supplies
the trainable language model inside stage four.

## Train the custom model

Create an isolated environment; activation is unnecessary:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-neural.txt
.\.venv\Scripts\python.exe -m framelm.train --data data/train.jsonl --validation data/validation.jsonl --preset micro --steps 200 --output state/tiny.pt
.\.venv\Scripts\python.exe -m framelm --config configs/tiny.json ask "What is an abelian group?" --json
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On Linux/macOS use `.venv/bin/python` in place of the Windows executable path.
For accelerator-specific installation use the official
[PyTorch installation selector](https://pytorch.org/get-started/locally/).
Set `--device cuda` for training and `"device": "cuda"` in your inference config
only when your installation and hardware support it. CPU is the default.

Each JSONL line contains one supervised example:

```json
{"prompt":"What is a group?","contexts":["A group has an associative operation, identity, and inverses."],"response":"A group has an associative operation, an identity, and inverses. [C1]"}
```

Responses must cite the supplied contexts. Keep validation prompts separate from
training prompts. The trainer rejects exact normalized prompt overlap, masks
prompt loss, clips gradients, evaluates held-out response loss, and writes weights
plus a `.metrics.json` report. It starts a fresh run each time; optimizer-state
resume is not implemented. It loads the examples into RAM and is intended for
small experiments. It rejects overlong examples instead of silently truncating.

The eight training examples and two validation examples check the mechanics only.
A short run may produce poor or unreadable output. Useful quality requires a much
larger curated corpus, broad language learning, task-specific examples, and
independent evaluation. Parameter count or a lower training loss does not
establish answer quality.

## Scale from small to large

The included custom-network presets have these architecture-derived counts:

| Preset | Parameters | Layers | Width | Heads | Total byte-token window |
|---|---:|---:|---:|---:|---:|
| micro | 692,096 | 2 | 128 | 4 | 2,048 |
| small | 5,329,664 | 6 | 256 | 8 | 2,048 |
| medium | 27,449,856 | 8 | 512 | 8 | 4,096 |
| large | 88,400,640 | 12 | 768 | 12 | 4,096 |

Change `--preset` to select one. Here, `large` means the largest included educational
network; it is not a contemporary large general-purpose model. Counts include
learned positions and tied embeddings. The formula is
`width * (261 + block_size) + layers * (12 * width^2 + 13 * width)`.

The full frame, context, and output must fit the byte window. Bytes are not words
or subword tokens. Reducing `context_char_budget` can help, but character counts
are not byte counts; the neural backend performs a final exact byte-budget check.
Increasing width, depth, or window changes checkpoint shapes. It does not enlarge
an existing trained model automatically: train a new model or use compatible
pretrained weights through a serving backend.

For practical pretrained small-to-large scaling, edit `configs/served.json`:

```json
{
  "backend": "ollama",
  "endpoint": "http://localhost:11434",
  "model": "YOUR_INSTALLED_MODEL_NAME",
  "candidate_count": 2,
  "max_new_tokens": 512,
  "context_char_budget": 6000,
  "include_extractive_fallback": true
}
```

With Ollama running and your chosen model already installed:

```powershell
python -m framelm --config configs/served.json ask "Explain the five stages of FrameLM." --json
```

Switch the model name to use a larger compatible served model while preserving
the five-stage pipeline. Model installation, weights and a live server are not
bundled. Hardware needs depend on architecture, precision, window, batch size and
serving software. Test them on the target machine; no hardware/latency benchmark
is claimed. The adapter uses the documented
[Ollama chat API](https://docs.ollama.com/api/chat) with nonstreaming responses.
Requests send the prompt and retrieved context text to the configured endpoint.

| Growth axis | Available now | Further engineering at larger scale |
|---|---|---|
| Model capacity | Four custom presets; replaceable served model | Efficient tokenization, KV cache, quantized inference, distributed training |
| Knowledge | On-disk full-text index; streamed documents during ingestion | Incremental ingestion, hybrid/vector retrieval, metadata filters |
| Resolution quality | Multiple generated candidates and an explicit lexical ranker | Learned reranking, evidence entailment checks, contradiction handling |
| Request volume | Single-request CLI | API service, resident model cache, batching, concurrency limits |
| Training volume | In-memory supervised examples | Streaming datasets, resumable checkpoints, distributed optimizer |

SQLite uses an inverted index rather than scanning every document per query.
Large single documents are still read into memory while indexing. The custom
Transformer recomputes the prefix for each generated token and has no KV cache;
use a mature serving engine for substantial inference workloads. These extension
points make the architecture scalable; this release is not a distributed system.

## Best-fit rule and its limits

For each candidate with at least one citation and no out-of-range citation IDs:

`score = 0.45 * query_coverage + 0.40 * lexical_support + 0.15 * valid_citations`

- `query_coverage`: fraction of query terms occurring in the answer.
- `lexical_support`: fraction of answer terms occurring in its cited contexts.
- `valid_citations`: 1 when all `[Cn]` references identify supplied contexts.

Select the highest score; deterministic candidate order breaks ties. The default
extractive backend emits one assembled candidate. Neural and served backends can
produce several candidates, with an optional extractive candidate added as a
fallback. Excerpts often win because this simple metric rewards source overlap.
The JSON result records which backend actually supplied the selected answer.

This is a lexical heuristic, not semantic verification or calibrated confidence.
A false statement can reuse the right words and cite a real source. It does not
reliably detect contradictions, prove entailment, or guarantee that the user's
constraints were met. A `resolved` status only means a candidate was selected.
Before generation the system abstains if retrieved query-term coverage is below
`min_query_coverage` (default 0.35). This threshold needs evaluation on your domain.

Source text is marked as data in the model instructions. That instruction is not
a prompt-injection security boundary. No retrieved code or model output is
executed by this application.

## Configuration and extension

Configuration files belong in a `configs/` directory directly under the project
root. Data paths are resolved relative to that root, not the terminal directory.
Absolute paths also work. Unknown settings are rejected. Shared defaults live in
`framelm/core.py`. `candidate_count` limits model calls per request; use 1 to reduce
generation work. `top_k` limits retrieved chunks; `context_char_budget` limits
included context text. These are resource controls, not service usage accounting.

Implement `Retriever.search(frame, top_k)` to replace retrieval and
`Backend.generate(frame, contexts, config)` to replace generation. Both can be
injected into `Pipeline`. `score_candidate` and `frame_prompt` are independent
replacement points. Context Studio provides a loopback-only web GUI. No automatic
scheduler or cloud deployment is included.

## Verification

```powershell
python -m unittest discover -s tests -v
```

In the build environment: **48 tests passed; 4 optional neural tests skipped**
because PyTorch was unavailable. The demo index and CLI were exercised. Generator
parity was checked against compiled copies of all three supplied C++ files, and
context lifecycle and HTTP workflows were tested. JavaScript passed syntax checks.
The remote test browser could not access the loopback server, so visual layout and
browser interactions remain unverified here. The
Ollama request/response contract was tested using a mock, not a live model server.
Neural execution and training have not been verified here. Optional tests cover
causal masking, gradient flow/checkpoint round-trip, response-label alignment,
and generation-window rejection when PyTorch is installed.

For a deployment decision, evaluate retrieval recall, answer correctness, source
support, abstention quality, constraint compliance, latency and memory on held-out
questions from your actual domain. Byte perplexity alone cannot measure these.

Implementation reference: PyTorch's
[TransformerEncoderLayer](https://docs.pytorch.org/docs/main/generated/torch.nn.modules.transformer.TransformerEncoderLayer.html)
provides the attention/feed-forward block used with a causal mask in this project.
