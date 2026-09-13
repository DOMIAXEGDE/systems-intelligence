# Language

`((Generator + Filter) == Complete) = True`

Language combines two related systems:

- **Omicron is the generator**: an engineering-logic research collection for Boolean systems, semantic structures, computational linguistics, arithmetic, typed data, axiom/fact/rule processing, and experiments in C, C++, Python, and PHP.
- **GPILC is the filter**: a dependency-free C++20 compiler for the General Purpose Instruction Language (GPIL). It evaluates one event instruction per nonblank source line and emits a JSON memory database only after proving that every sequential-string ID decodes to its exact original UTF-8 value.

The repository also includes the PHP Memory Square application, the complete 209-event reference database, an interactive browser map, executable GPIL examples, and the generated database report.

## Clean build after cloning

### Windows requirements

- Git
- CMake 3.24 or newer
- Ninja
- a C++20-capable `g++` toolchain, such as MSYS2 MinGW-w64

Clone and build from a PowerShell terminal:

```powershell
git clone https://github.com/DOMIAXEGDE/Language.git
Set-Location Language
cmake --fresh -S compiler -B build/compiler -G Ninja -DCMAKE_CXX_COMPILER=g++
cmake --build build/compiler
ctest --test-dir build/compiler --output-on-failure
./build/compiler/gpilc.exe --self-test
```

The executable is created at `build/compiler/gpilc.exe`. The `--fresh` configuration prevents an old CMake generator cache from contaminating a clean or reconfigured build. The equivalent convenience command is:

```powershell
./build-gpilc.ps1
```

A prebuilt Windows executable is included at `bin/gpilc.exe` for convenience, but building locally is the reproducible and preferred path.

## Compile a GPIL program

```powershell
./build/compiler/gpilc.exe examples/demo.gpil --output build/demo-events.json
```

Each nonblank GPIL line has this shape:

```gpil
event "Sum" : add(40, 2)
event "Container" : containerize("space-time")
event "Recovered" : decontainerize($2)
event "Proof" : assert_equal($3, "space-time")
```

Each compiled event records its timestamp, label, value, stable event ID, arbitrary-precision sequential-string ID, backward-computed value, exact-match proof, ordered alphabet, metrics, instruction provenance, and square-map position.

The formal grammar, built-in instruction arrays, semantics, and forward/backward shortlex mathematics are documented in [`docs/GPIL.md`](docs/GPIL.md).

## Sequential-string ID model

For an ordered alphabet of size `b`, strings are ranked in shortlex order: first by length and then by base-`b` symbol rank. The empty string has ID `0`. For a nonempty string `x` of length `n`:

```text
S_n   = sum(b^k, k = 1 .. n-1)
R(x)  = the fixed-width base-b rank of x
ID(x) = 1 + S_n + R(x)
```

The compiler locates the ID's length bucket, subtracts one for the within-bucket offset, repeatedly divides by `b`, maps each remainder back to its alphabet symbol, and requires exact UTF-8 equality before emission. IDs remain decimal strings throughout, avoiding JSON and JavaScript precision loss.

## Instruction scope

GPIL includes composable operations for:

- arbitrary-precision addition, subtraction, multiplication, division, and modulo;
- directional polarity;
- reversible containerization and payload transformation;
- UTF-8 text construction, concatenation, replacement, reversal, and symbol measurement;
- constructive Boolean logic, equality assertions, and conditional selection;
- semantic triples;
- sequential-string encoding, decoding, and round-trip verification;
- backward event references and square-map coordinates.

Calls can be nested, and `$n` references the value of an earlier event ordinal.

## Omicron

The [`omicron/`](omicron/) directory contains the source and data side of the generator. It includes:

- C implementations of permutation/generator pipelines and output sinks;
- C++ command-line systems, parsers, Boolean-expression tooling, arbitrary-precision integer work, and JSON/fact/rule processing;
- Python Boolean-function, circuit, transition-machine, tensor, axiom, semantic, and data-generation experiments;
- PHP and PowerShell utilities;
- truth-table, basis-tensor, Book Sport, axiom, acceptance-event, dictionary, image, and research artifacts.

Omicron is a research and prototyping collection rather than one monolithic executable. Individual numbered programs may represent different generations or experiments and can have their own runtime assumptions. Review the header and command-line help in the selected source before running it.

The upstream `omicron/doc` directory is intentionally excluded from this repository, as requested. Omicron's source, data, root documentation, images, and license remain included.

## PHP Memory Square

With PHP 8.5 or newer:

```powershell
php -S 127.0.0.1:8080
```

Open <http://127.0.0.1:8080>. The server process needs write access to `data/events.json`.

The application can:

- validate user-defined Unicode alphabets;
- encode and decode arbitrary-precision sequential IDs;
- append proven events under file locks;
- search and inspect event records;
- display the database as a tabular or pannable/zoomable square map;
- export the complete database as `report.pdf`.

## Repository layout

| Path | Purpose |
| --- | --- |
| `compiler/` | C++20 GPIL lexer, parser, evaluator, arbitrary-precision arithmetic, proof engine, JSON writer, and tests |
| `bin/gpilc.exe` | Prebuilt static Windows compiler |
| `docs/GPIL.md` | Language and mathematics specification |
| `examples/` | Executable demo and one-line-per-event system manifest |
| `src/` | PHP arbitrary-precision arithmetic, sequential IDs, event store, and PDF generator |
| `api.php`, `index.php`, `assets/` | PHP/JavaScript/CSS web application |
| `data/events.json` | 209 proven event records and the complete 15 x 15 memory map |
| `output/pdf/report.pdf` | Complete 234-page database and component report |
| `tests/` | PHP regression tests |
| `tools/` | Database rebuild and report-export utilities |
| `template/` | Earlier compiler/interface logic-pattern guidance |
| `omicron/` | Filtered Omicron engineering-logic generator tree; `omicron/doc` excluded |

Generated build directories, CMake caches, Python bytecode, temporary PDF renders, and `omicron/doc` are ignored by Git.

## Validation

Run the compiler and PHP checks:

```powershell
ctest --test-dir build/compiler --output-on-failure
php tests/run.php
```

The reference database contains 100 preserved constructive-mathematics events and 109 executable instruction/component events. Its map has 225 cells: 209 occupied and 16 explicitly vacant.

## Applications

The codebase can support reversible knowledge/event identifiers, semantic audit trails, deterministic corpus indexing, constructive proof demonstrations, database and map visualization, compiler/language experimentation, Boolean-logic education, circuit research, computational-linguistics experiments, and generation/filter pipelines.

Omicron includes experimental research programs and data. Evaluate individual modules before production or safety-critical use.

## License

The repository root is licensed under the [MIT License](LICENSE). Omicron also includes its own [`omicron/LICENSE`](omicron/LICENSE); retain the applicable notices when redistributing its contents.
