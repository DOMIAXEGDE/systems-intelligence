# General Purpose Instruction Language (GPIL) 1.0

GPIL is the executable, event-oriented instruction language for Memory Square. Its compiler is `gpilc`, a dependency-free C++20 program. The source and PHP runtimes share the same shortlex ranking model and the same JSON event contract.

## Fundamental contract

Every nonblank physical source line is one event. A line is parsed, evaluated, ranked, decoded, verified, and only then emitted. A failed parse, evaluation, alphabet check, or reverse proof stops compilation without producing a partial output file.

```ebnf
program    = { blank-line | event-line } ;
event-line = "event" string ":" expression [ "alphabet" string ] [ ";" ] newline ;
expression = string | signed-decimal | reference | call ;
reference  = "$" positive-decimal ;
call       = identifier "(" [ expression { "," expression } ] ")" ;
```

References are 1-based event ordinals and may only point backward. Because every nonblank line is exactly one event, `$1` names the first event line, `$2` the second, and so on. Calls may be nested. String escapes are `\"`, `\\`, `\n`, `\r`, and `\t`. The optional event alphabet defaults to the 95 printable ASCII symbols in code-point order.

Example:

```gpil
event "Sum" : add(40, 2)
event "Container" : containerize("space-time")
event "Recovered" : decontainerize($2)
event "Proof" : assert_equal($3, "space-time")
```

## Mathematical sequential-string ID

Let the ordered, duplicate-free alphabet be `A = (a_0, ..., a_(b-1))`, with `2 <= b <= 1024`. Let a nonempty value `x = x_0 ... x_(n-1)`, `1 <= n <= 4096`, and let `d_i` be the zero-based position of `x_i` in `A`.

The number of strings in all shorter nonempty buckets is

`S_n = sum(k = 1 to n - 1) b^k`.

The base-`b` rank within the length-`n` bucket is

`R(x) = sum(i = 0 to n - 1) d_i * b^(n - 1 - i)`.

The sequential-string ID is

`ID(empty) = 0`

`ID(x) = 1 + S_n + R(x)` for nonempty `x`.

All quantities are arbitrary-precision decimal strings. No JSON number is used for the ID, so PHP, JavaScript, and C++ cannot round it.

## Backward computation

Given decimal ID `N`:

1. Normalize leading zeroes. Return the empty string when `N = 0`.
2. Set `remaining = N`, `length = 1`, and `bucket = b`.
3. While `remaining > bucket`, subtract `bucket`, increment `length`, and multiply `bucket` by `b`.
4. Set `offset = remaining - 1`.
5. Allocate `length` symbols, initially `a_0`.
6. From the last position to the first, compute `(offset, remainder) = divmod(offset, b)` and write `a_remainder`.
7. Concatenate the symbols.
8. Require the decoded byte string to equal the original UTF-8 input exactly.

The bucket ranges are disjoint and exhaustive. Within a bucket, fixed-width base-`b` positional notation is bijective. Therefore ranking and unranking are inverse functions over all finite strings in `A` within the explicit context limit.

## Instruction arrays

| Array | Purpose | Built-ins |
| --- | --- | --- |
| 1 | Construction | `add`, `apply_directional_polarity`, `containerize`, `decontainerize` |
| 2 | Container transformation | `transform_container` |
| 3 | Arbitrary-precision arithmetic | `subtract`, `multiply`, `divide`, `modulo` |
| 4 | Text and identity | `literal`, `identity`, `concat`, `symbol_length`, `reverse`, `replace` |
| 5 | Constructive logic | `equal`, `not`, `and`, `or`, `select`, `assert_equal` |
| 6 | Semantics | `semantic_triple` |
| 7 | Sequential IDs | `sequential_encode`, `sequential_decode`, `sequential_verify` |
| 8 | Event memory and mapping | `$n` references, `map_coordinate`, automatic event capture, automatic square-map construction |

`containerize(x)` returns `GPDB1:<UTF-8-byte-count>:<x>`. `decontainerize` validates both the marker and byte count. `transform_container` accepts `reverse`, `uppercase`, `lowercase`, `append`, `prepend`, or `replace`, transforms only the payload, and emits a new valid container.

Arithmetic is signed and arbitrary precision. Division truncates toward zero and modulo has the dividend's sign. Logic uses only the strings `true` and `false`. `select` is constructive choice; `assert_equal` is a checked proof obligation. `semantic_triple(s,p,o)` emits the canonical text `s <p> o`.

## Event schema

Every emitted event contains:

- `time-stamp`, `label`, `value`, and stable `ID`;
- decimal `sequential-string ID`;
- `backward-computed value` and a `matches-value` proof Boolean;
- the complete ordered `alphabet`;
- alphabet-symbol, value-symbol, ID-digit, and signed-64-bit-native metrics;
- source language, physical line, root operation, and exact source line.

The database also contains schema and update timestamps, square-map row and column dimensions, total/occupied/vacant counts, and the complete row-major cell list. An occupied cell stores the event ID; a vacant cell is `null`.

## Compiler pipeline

`gpilc` reads UTF-8 source, lexes each physical line, builds an expression tree, rejects forward references, evaluates built-ins, validates the selected event alphabet, computes the shortlex ID, un-ranks it, checks exact equality, constructs the event, builds the square map, and serializes lossless UTF-8 JSON. Syntax failures return exit code 2; I/O or configuration failures return exit code 1.

Build and verify:

```powershell
cmake --fresh -S compiler -B build/compiler -G Ninja -DCMAKE_CXX_COMPILER=g++
cmake --build build/compiler
ctest --test-dir build/compiler --output-on-failure
build/compiler/gpilc.exe examples/demo.gpil --output build/demo-events.json
```
