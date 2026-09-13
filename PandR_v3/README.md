# Point & Resolve blueprint handoff

For the implemented application's explicit, implicit, piecewise and combined
function plotting, see [PLOTTING.md](PLOTTING.md) and
[the runnable plotting examples](examples/plot_functions.py).

Start with [2d-llm-blueprint.md](2d-llm-blueprint.md). It specifies the graphical Python P&R application, based on the inspected `operating-system` architecture and the supplied diagram.

The blueprint defines fixed signed-prime points; horizontal `v` and `h` contact measurements; transformations/composition; ordered sequence syntax; at most seventeen layers; original pixel alphabets; local smart contracts; JSON sessions/replay; Python operator scripts; state-to-state signals; and text, image, audio, video and code exporters.

This folder currently contains the **blueprint and runnable reference verification tools**. The full application and the proposed `pandr` package described in the blueprint are the next implementation, not files already delivered here.

## Included evidence and working tools

- [Conversion mechanics](research/conversion-mechanics.md): exact forward/reverse formulas, alphabet rules, observed website behavior and limits of verification.
- [Python reference codec](research/reference_codec.py): working offline text/rank conversion with arbitrary-precision decimal strings.
- [Codec vectors](research/codec-vectors.json): 35 cases verified against the published PHP codec, with 209 additional published records retained alongside them.
- [Source provenance](research/provenance.json): scraped website assets and pinned repository source URLs/hashes; original source license is retained.
- [Example verifier](research/verify_blueprint_examples.py): exact geometry/composition fixtures, sample sequence and byte-framing checks, and provenance integrity.
- [Verification report](research/blueprint-verification.json): generated example results and local architectural source inventory.
- [Reference diagram](research/2d-llm-reference.png): unchanged copy of the supplied image.

From `D:\largeLanguageModel\PandR`, run:

```powershell
python -B research/reference_codec.py --self-check
python -B research/reference_codec.py --alphabet ab --encode ab
python -B research/reference_codec.py --alphabet ab --decode 0004
python -B research/verify_blueprint_examples.py
php research/verify_published_codec.php
```

The PHP check requires PHP with `mbstring`; the Python tools use the standard library. Source-code execution for these checks is local and does not contact the website.

The live site identifies GPIL/1.1; its linked repository source is the independently verified shortlex compatibility target. Identical deployed PHP behavior has not been established. Both `v` and `h` are defined as horizontal distances in the blueprint, preserving the wording of the P&R specification.
