# Deterministic text / number conversion research

Research date: 2026-09-09 UTC. Target: [Domalec X1](https://domalec.com/content3/x1/index.php). All saved evidence is available offline in this directory. The original repository material is distributed under its accompanying [MIT license](repository-LICENSE), copyright 2026 Dominic Alexander Cooper.

## Evidence and its limits

The requested URL returned HTTP 200 and currently serves **GPIL Workbench**, a persistent browser editor. Its HTML describes parsing and evaluating event records, ranking their values, reversing them, and emitting JSON only after proof checks. The linked JavaScript contains editor, persistence, and rendering mechanics; it does **not** contain the ranking implementation. It uses `GET ./index.php?api=1` for bootstrap and POST actions for saving and compiling. No POST action was called in this research.

The ordinary bootstrap GET returned public configuration, examples, and a fresh anonymous workspace. The response itself reports `workspace-created`; thus the server initializes anonymous JSON workspace state as part of a normal read. No existing browser session, user file, or account workspace was opened or modified. No source was compiled on the deployed website.

The bootstrap configuration explicitly links [DOMIAXEGDE/Language](https://github.com/DOMIAXEGDE/Language). Its inspected, pinned commit is [`31d13acd4329364f649d97f3a6806d5fa5b3d5d1`](https://github.com/DOMIAXEGDE/Language/commit/31d13acd4329364f649d97f3a6806d5fa5b3d5d1). This repository contains the exact PHP ranking implementation, arithmetic helper, tests, formal specification, and 209 golden event records.

**Verified scope:** the formula below is established by published repository source and was executed locally against that source. It is also independently reproduced in Python and checked against all 209 published records. The live deployed PHP source was not exposed by the page; matching deployment identity is **unverified**. The live configuration says `gpilc-php 1.1.0`, `GPIL/1.1-multiline`, while the repository documentation describes `GPIL/1.0` and one physical line per event. Treat the live multiline behavior as observed configuration and the pinned ranking behavior as a reproducible compatibility target; do not conflate their versions.

| Local evidence | What it establishes |
| --- | --- |
| `domalec-x1-observed.html` | Current workbench UI, linked asset, API endpoint and multiline description |
| `domalec-workbench-observed.js` | GET bootstrap, JSON POST action names, save queue, proof rendering; no local codec arithmetic |
| `domalec-bootstrap-observed.json` | Live product/repository link, default alphabet, operation arrays, limits, example sources |
| `domalec-repository-commit-observed.json` / `domalec-repository-tree-observed.json` | Pinned commit and repository file provenance |
| `repository-src-SequentialStringId.php` | Exact rank/unrank implementation, alphabet validation, context limits, reverse proof |
| `repository-src-BigNatural.php` | Nonnegative decimal-string validation, leading-zero normalization and arbitrary-precision operations |
| `repository-docs-GPIL.md` | Mathematical shortlex contract and source-language framing |
| `repository-tests-run.php` | Published base-2 vectors, Unicode/long-input tests, all-record verification contract |
| `repository-data-events.json` | 209 independently published value/alphabet/ID fixtures |
| `provenance.json` | SHA-256 of each saved source snapshot and source URLs |

## Alphabet and symbol contract

The published codec accepts an **ordered**, duplicate-free string of 2 to 1024 Unicode code points as its alphabet. Order determines numeric digit values. PHP validates UTF-8 and uses `mb_str_split`; it splits code points, not bytes or user-perceived grapheme clusters. An emoji sequence or combining-accent spelling can therefore use several symbols.

The observed live default is exactly the 95 printable ASCII characters, U+0020 through U+007E in ascending order. Space is digit 0; `!` is digit 1; `A` is digit 33; `~` is digit 94. Punctuation, case, repeated characters, and leading/trailing spaces are preserved. Newline, carriage return, and tab are absent from that default, but can belong to an explicitly supplied alphabet. The live multiline example does exactly this for newline.

No NFC/NFD normalization, trimming, case folding, transliteration, or grapheme joining occurs in the inspected codec. For alphabet `é`, `e`, U+0301 (three ordered code points), composed `é` has ID `1`, while decomposed `e` + U+0301 has ID `9`. They can look alike and still encode differently.

Inputs may contain 0 through 4096 code points. Every input code point must belong to the chosen alphabet. Invalid UTF-8, duplicate/undersized/oversized alphabets, symbols outside the alphabet, and unsupported context sizes cause errors.

## Forward: string to sequential ID

Let `A = (a_0, ..., a_(b-1))`, where `b = len(A)`. For a nonempty string of length `n`, let `d_i` be the zero-based alphabet index of its symbol at position `i`.

```text
ID("") = 0
shorter_count(n) = sum(b**k for k in range(1, n))
rank(x) = sum(d_i * b**(n - 1 - i) for i in range(n))
ID(x) = 1 + shorter_count(n) + rank(x)
```

Strings are ordered first by length, then by alphabet order (*shortlex*). The most significant positional digit is the leftmost symbol. The extra length-bucket offset preserves leading symbols whose digit is zero. For alphabet `01`, `001` is distinct from `1`; its ID is `8`.

One efficient equivalent for nonempty input is `shorter_count = (b**n - b) // (b - 1)` and left-to-right accumulation `rank = rank*b + digit`. This uses exact integer arithmetic.

## Reverse: sequential ID to string

The source accepts a **string of ASCII decimal digits**, with no sign, decimal point, exponent, or whitespace. Leading zeros are accepted and stripped; an all-zero string normalizes to `0`. Empty ID strings and non-ASCII decimal digit characters are rejected. These details were checked against the actual PHP source locally.

```text
N = normalize_decimal(ID)
if N == 0: return ""
remaining = N
length = 1
bucket = b
while remaining > bucket:
    remaining -= bucket
    length += 1
    reject if length > 4096
    bucket *= b
offset = remaining - 1
allocate exactly `length` symbol positions
for position from length-1 down to 0:
    offset, digit = divmod(offset, b)
    output[position] = A[digit]
return concatenation(output)
```

The length bucket supplies the fixed width needed to recover leading zero-valued symbols. The API's reverse computation is **unranking**, not reversing the order of text characters. GPIL also advertises a separate `reverse` text operation; these operations must remain distinct.

The inspected `encodeVerified` method computes an ID, decodes it with the same alphabet, and rejects any result unequal to the original input. IDs are persisted as decimal JSON **strings**, including `"0"`, to preserve precision across PHP, JavaScript, Python, and other runtimes. `decode(encode(text)) == text`; `encode(decode(id)) == canonical_decimal(id)`, so an input ID such as `"0004"` returns canonical `"4"` on re-encoding.

The ordered alphabet is essential to reversal and must be retained with the ID, or resolved through a durable versioned registry. An ID by itself does not identify the original text across different alphabets.

## Reproduced examples

All examples below were executed using the downloaded pinned PHP source and then cross-checked using the independent Python implementation.

| Alphabet | Text | Decimal ID |
| --- | --- | --- |
| `ab` | empty string | `0` |
| `ab` | `a`, `b`, `aa`, `ab`, `ba`, `bb`, `aaa` | `1`, `2`, `3`, `4`, `5`, `6`, `7` respectively |
| `ba` | `ab` | `5` |
| `01` | `001` | `8` |
| `αβ🙂🚀 ` (ends with space) | `🚀 α🙂β🚀` | `15839` |
| Printable ASCII U+0020..U+007E | one space / `A` / `~` / two spaces | `1` / `34` / `95` / `96` |
| Printable ASCII U+0020..U+007E | `Hello, P&R!` | `2499455179395414973772` |
| `a`, LF, TAB, SPACE | `a`, LF, TAB, SPACE | `112` |

For `ab` with alphabet `ab`, `b=2`, `n=2`, shorter count is `2`, positional rank is `0*2+1=1`, and ID is `1+2+1=4`. Reverse ID `4`: subtract the first bucket `2`, obtain length `2`, subtract `1` for offset `1`, then write its two base-2 digits `01` as `ab`.

## Offline verification and files

`codec-vectors.json` contains 20 round-trip vectors, 2 leading-zero aliases, and 13 rejection vectors, with their actual PHP outcomes. `verify_published_codec.php` regenerates those vectors by executing the saved source with PHP + mbstring and also checks all 209 published records. No network access is involved.

`reference_codec.py` independently implements the pinned shortlex contract using Python's standard library. Its CLI returns JSON strings so empty values and control characters remain inspectable. It avoids Python's global decimal-digit limit by converting large integers in short decimal chunks rather than disabling a global safety setting. This matters: an allowed alphabet of 1024 symbols and a 4096-symbol input can produce a 12,331-digit ID.

From `D:\largeLanguageModel`:

```powershell
python PandR/research/reference_codec.py --self-check
python PandR/research/reference_codec.py --alphabet ab --encode ab
python PandR/research/reference_codec.py --alphabet ab --decode 0004
php PandR/research/verify_published_codec.php
```

Observed results: PHP 8.5.1 + mbstring passed 35 vectors and 209 published records. Python 3.14.5 passed the same 35 vectors and 209 records, plus the maximum-size round trip (12,331 decimal digits), oversize input rejection, oversize ID rejection, and surrogate rejection. The published source's full application test suite was not run; only the codec and its saved fixture records were exercised.

## Applying this to P&R

Use this arithmetic as an explicitly versioned **text interoperability codec**, for example `domalec-shortlex-repo-v1`, with commit provenance. It is deterministic reversible addressing of strings, not a learned model, compression guarantee, semantic proof, or efficient procedure for finding desirable output. Enumerability of finite strings does not bound the search required to find one. The source's 4096-symbol limit is finite and unrelated to P&R's maximum of 17 layers.

The proposed pixel-created P&R glyph alphabet is a **new layer**, not a behavior observed on the website. Store each glyph as canonical bitmap data plus a stable symbol ID, dimensions, palette, and rendering metadata. Define alphabet order over those stable symbol IDs. The same shortlex arithmetic can rank a finite ordered **sequence of glyph IDs** by replacing Unicode code-point digits with explicit glyph indices; that extension needs its own codec ID, tests, and manifest. Do not pretend arbitrary custom glyphs are already Unicode text or allow bitmap edits to silently change a pinned alphabet.

For each emitted value, preserve `codec_id`, codec version, `alphabet_id`, alphabet version/hash, ordered symbols or a durable registry reference, `symbol_count`, `sequential_id` as a decimal string, and a reverse-check result. Export adapters can map glyph IDs to text, image pixels, audio samples, video frames, or code bytes under explicit versioned contracts. Where an output format needs byte-exact preservation, define a byte codec rather than silently UTF-8-decoding arbitrary bytes. Newline normalization, image metadata removal, lossy media encoding, and Unicode normalization must never be implicit parts of a claimed exact round trip.

The website establishes no mapping from P&R point geometry, prime distances, `v17`/`h37` signals, function transformations, or P&R state-to-state communication to those symbol indices. Those mappings must be specified in the blueprint as new, operator-controlled deterministic contracts. Sequence concatenation and arithmetic addition must remain distinguishable: summing numeric signal IDs would destroy ordering and repetitions needed for exact reversal.
