# FrameLM Context Studio

Version 0.3 adds a Chat tab powered by these contexts. See `CHAT.md` for usage.

This update combines the distinct generation logic in your `4.cpp`, `5.cpp`, and
`6.cpp` into FrameLM's own Python package and adds a local browser GUI. The original
C++ sources are preserved in `reference_sources/` for reference and comparison;
they are not required to run the application.

## Start on Windows

Extract the archive and open the `frame_lm` folder. Double-click
`start-context-studio.cmd`, or run in PowerShell:

```powershell
python -m framelm studio
```

The application opens the default browser and prints its local address. Keep the
terminal open while using it; Ctrl+C stops the server. If the default port is busy:

```powershell
python -m framelm studio --port 8766
```

Python 3.10+ with SQLite FTS5 is required. The GUI uses modern browser features
including native dialogs. It has no pip dependencies, CDN resources, telemetry,
or model downloads. It binds only to the local computer.

## What each source contributes

| Source | Unique logic retained | Context Studio control |
|---|---|---|
| `4.cpp` / configure_1 | Base-N ordinal decoding over original, safe, decimal, or lowercase alphabets; fixed-width symbol combinations | Symbol combinations, alphabet, width |
| `5.cpp` / configure_2 | Decimal enumeration with leading zeroes retained | Numeric handles, width |
| `6.cpp` / configure_3 | Named object/input/flow/output stages; custom Cartesian, literal, repeat, reverse; prefix/suffix; separator metadata | Generation flow, named stages, text and templates |
| All three | Start/limit windows, total-count planning, uint64 overflow guards, explicit execution, and FNV-1a hashes | Preview, append batch, provenance |

These are Cartesian products with repetition, not permutations without repetition.
For alphabet `01` and width 3, the sequence is `000, 001, 010, 011, 100, 101,
110, 111`. Numeric width 7 retains values such as `0000007` as strings.

The source `repeat` flow emits N separate records with the same payload. It does
not concatenate the payload N times. Literal and reverse produce one record;
their width does not affect the output count. Prefix/suffix wrap the payload.
Separator remains provenance metadata, as in `6.cpp`; it is not inserted between
records in the retrieval index or JSONL export.

Generation is ported into Python for direct integration. The GUI replaces the
original menu, recipes replace the old `.fabric` configuration syntax, and the
context database replaces arbitrary output-file/stdout targets. The CLI still
prints dry-run previews and execution summaries. Original `.fabric` and cppdb
files are not imported by this release.

ASCII values, ordering, and FNV hashes were compared with actual compiled C++
outputs. For non-ASCII input, Cartesian and reverse work on Unicode code points
instead of C++ bytes, keeping UTF-8 output valid. This does not preserve grapheme
clusters such as combining accents or multi-code-point emoji. FNV-1a hashes still
operate on UTF-8 bytes. FNV is retained for source compatibility; it is not a
cryptographic identity check.

## Work with contexts

**Append:** choose Append context, enter a title and context text, optionally add
tags, and save. Leave Enabled checked to make it available to FrameLM immediately.

**Edit:** click a title or Edit. Saving replaces the context's indexed text in the
same transaction while retaining its stable ID and generation/source provenance.
If another window has already changed the record, the stale save is rejected;
close and reopen the editor to load the current revision.

**Disable / enable:** use the row action or the editor checkbox. Disable removes
its retrieval chunks immediately without deleting its stored text. Re-enable
recreates the chunks. Rebuilding the index cannot resurrect disabled records.
The text remains available in the Studio catalogue and exports.

**Find:** search titles, bodies, or tags and filter by enabled state. The catalogue
loads 40 records per page. Search is a literal substring search, separate from
FrameLM's BM25 retrieval. Record bodies and full provenance load only when opened.

**Import new files:** on first launch, existing `.txt` and `.md` files in the
configured `contexts/` folder are adopted as managed records, one per document.
Use Import new files to adopt additional files later. Existing managed records
are never overwritten by this action. Individual documents may be at most 1 MB;
split larger documents before importing.

Once imported, the database version takes precedence over the original file.
Edit the context in Studio from then on. Changing or removing the original file
does not change or remove its managed record. This is intentional: the file must
not undo an edit or reactivate a disabled context. The source files themselves are
not modified. CLI-only documents that have never been adopted retain FrameLM's
original file/index behavior.

**Export JSONL:** downloads all managed records, including disabled ones and their
metadata, using JSON escaping to preserve newlines, tabs, backslashes and Unicode.
The export is a portable data copy; JSONL restoration/import is not provided in
this GUI. For a complete restorable workspace backup, use the database procedure below.

## Generate contexts

1. Open Generator and select a flow.
2. Set the input/alphabet, width, object name, prefix/suffix, templates, and range.
3. Preview generation to see total size, the selected range, next start ordinal,
   and the first ten rendered records.
4. Choose Append generated contexts. The entire batch is committed together.
5. Edit or disable individual results in Contexts. Use Retrieval check to inspect
   which enabled contexts FrameLM actually references.

Save recipe downloads a JSON file. Load recipe reads it into the form. Recipes
saved by the GUI contain the full form configuration. Numeric fields travel as
decimal strings so 64-bit ordinals are not rounded by JavaScript. The server also
accepts integer JSON fields from Python or other exact-integer clients.

The generation range supports the C++ uint64 count limit and per-flow width caps.
More than 100,000 selected records requires the Allow large batch checkbox, and a
desktop job has a hard maximum of one million records. Use consecutive start/limit
windows for larger spaces. Preview never iterates the whole possible space.
Only one batch runs at a time. Cancellation or validation failure rolls back the
entire batch, including its index changes. During a large write, other changes
may wait for the SQLite writer lock; reads can continue. This is a desktop system,
not a distributed generator.

Generating the same recipe and ordinal again skips the existing record. Its edits
and disabled state are preserved. Changing only start, limit, all, allow_large, or
enabled does not create a different generation identity. Changing the payload,
templates, tags, stage names, or other content settings creates a different recipe
identity and appends new records. Re-enabling existing results is an explicit
context action, not a side effect of regeneration.

## Give generated data a meaning

| Template field | Meaning |
|---|---|
| `{payload}` | Raw symbol, numeric, literal, repeated, or reversed value |
| `{value}` | Prefix + payload + suffix |
| `{ordinal}` | Unpadded decimal ordinal |
| `{handle}` | Ordinal padded to the selected handle width, never truncated |
| `{object_name}` | Name of the generated object family |
| `{input_name}` | Named input stage |
| `{flow_name}` | Named processing stage |
| `{output_name}` | Named context output |

Example: select Numeric handles, width `7`, start `0`, limit `10`, and use:

```text
Title: {object_name} / {handle}
Context: Object handle {value} maps to ordinal {ordinal}.
```

This creates descriptions such as “Object handle 0000003 maps to ordinal 3.”
No `eval`, scripts, arbitrary Python expressions, attribute access or format
conversions are supported in templates. Use `{{` and `}}` for literal braces.

Generating combinations does not generate factual knowledge. A text template can
describe the generated data or express a rule you supply; it does not validate
that rule. Prefix/suffix text is literal. Input text accepts actual newlines and
tabs; only the separator field interprets visible `\n`, `\r`, and `\t` escapes.
Empty/whitespace-only context output is rejected, rolling back that batch; use a
descriptive template when enumerating spaces or newlines.

## Command-line generation

```powershell
# Preview only; writes no records.
python -m framelm generate --recipe configs/generator.json

# Append the selected range and update retrieval.
python -m framelm generate --recipe configs/generator.json --execute

# All normal FrameLM backends see the same enabled contexts.
python -m framelm ask "What is object handle 0000003?"
```

Use the same `--config` before the subcommand when selecting a different project
index. Context Studio's built-in Retrieval check always uses the local extractive
backend. It does not call the optional remote/served backend.

## Data and backup

The existing configured `index_path` (default `state/contexts.sqlite3`) now holds
both the authoritative managed context catalogue and the derived retrieval index.
**Do not delete it as though it were a disposable index.** Use the index command
or Rebuild index button instead; these preserve the context catalogue.

For a consistent backup, stop Context Studio and other FrameLM processes, then
copy the entire `state/` folder. To restore, stop those processes and restore the
saved folder. Include any SQLite `-wal` or `-shm` sidecar files if present. Keep
your custom configs, recipes, and original context documents with the backup.
Generated data is not included in this distribution; the database is created
locally on first use.

Managed context data is searchable and editable through one database. Its
generation provenance includes the original recipe, ordinal, handle, raw payload,
rendered value, FNV hash and batch ID. Later edits change the current text while
keeping the original provenance; the hash describes the generated rendered value,
not a subsequently edited body or the outer text template. Revision numbers
prevent stale overwrites; they are not a full version-history/undo system.

## Verification and limitations

The updated suite reports **48 passed, 4 skipped**. It checks source-output parity,
Unicode, 64-bit range handling, whitespace escaping, append/edit/disable/re-enable,
rebuild behavior, duplicate generation, stale edits, cancellation rollback,
persistence, pagination, HTTP generation and retrieval, and request validation.
The four skipped tests require optional PyTorch, which is not installed here.

JavaScript passed `node --check`. The local HTTP service and its assets were
tested directly. The remote test browser rejected the local loopback address;
visual layout and browser interactions have not been verified in this environment.
No Windows executable was built, and Windows execution itself has not been tested.
The Python implementation and original C++ comparisons were exercised on Linux.
