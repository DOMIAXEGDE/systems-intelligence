# systems-intelligence
Intelligence with Control

A context, chat, and generation workspace with a P&R Controller for inspecting
application state, replaying recorded changes, exporting function plots and GIFs,
and running approved Python skills with scoped autonomy.

The repository contains both packages so the controller uses P&R directly as a
shared dependency. Python 3.11 or newer is required.

| Directory | Contents |
| --- | --- |
| [systems_intelligence](systems_intelligence/) | Current FrameLM application, browser workspace, controller, tests, and examples |
| [PandR_v3](PandR_v3/) | Shared P&R expression, plotting, codec, geometry, script, signal, and replay utilities |
| [misc/frame_lm](misc/frame_lm/) | Earlier FrameLM source retained for reference |

## Build and start on Windows

From the repository root in PowerShell:

```powershell
.\systems_intelligence\build.ps1
.\systems_intelligence\start-context-studio.cmd
```

The build installs both packages, runs their tests, and creates distributions.
Virtual environments, local databases, sessions, and generated build outputs stay
local. The included contexts and training data are demonstration fixtures.

See the [Controller guide](systems_intelligence/CONTROLLER.md),
[application README](systems_intelligence/README.md),
[P&R README](PandR_v3/README.md), and [plotting guide](PandR_v3/PLOTTING.md).
