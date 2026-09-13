FrameLM receives a prompt, frames the prompt, retrieves related contexts, resolves the frame using those contexts, and outputs the best-scoring response candidate.

A prompt frame records the original prompt, an intent, query keywords, and a requested output format. The starter framer uses rules and preserves the original request for the model.

Context retrieval uses a SQLite FTS5 full-text index and BM25 ranking. Contexts are excerpts from local UTF-8 text or Markdown documents. Every retrieved context has a source and citation identifier.

Resolution can extract relevant sentences or generate candidate answers with a language model. The best-fit score combines query-term coverage, lexical overlap with cited evidence, and valid citation identifiers. This score is a heuristic, not a probability that an answer is correct.

Scaling FrameLM can increase the neural model width, depth, and attention heads, or replace the neural backend with a larger served model. Adding documents expands accessible context without changing model weights. Larger architectures require newly trained or compatible pretrained weights.
