import argparse
import json
import sys
from pathlib import Path
from .core import Pipeline, SQLiteRetriever, load_config


def main():
    parser = argparse.ArgumentParser(description="FrameLM five-stage context resolution")
    parser.add_argument("--config", type=Path, default=Path("configs/demo.json"))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("index", help="Rebuild the context index")
    studio = sub.add_parser("studio", help="Open the local Context Studio GUI")
    studio.add_argument("--port", type=int, default=8765)
    studio.add_argument("--no-browser", action="store_true")
    generate = sub.add_parser("generate", help="Preview or append contexts from a JSON recipe")
    generate.add_argument("--recipe", type=Path, default=Path("configs/generator.json"))
    generate.add_argument("--execute", action="store_true", help="Append records; otherwise preview only")
    ask = sub.add_parser("ask")
    ask.add_argument("prompt")
    ask.add_argument("--json", action="store_true", help="Show the frame, contexts, candidates and score")
    args = parser.parse_args()
    try:
        cfg = load_config(args.config)
        if args.command == "studio":
            from .studio import serve
            serve(cfg, args.port, not args.no_browser)
        elif args.command == "generate":
            from .generator import Recipe
            from .context_store import ContextStore
            recipe = Recipe.from_dict(json.loads(args.recipe.read_text(encoding="utf-8")))
            result = (ContextStore(cfg["index_path"]).append_generated(recipe)
                      if args.execute else recipe.preview())
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "index":
            count = SQLiteRetriever(Path(cfg["index_path"])).build(Path(cfg["context_dir"]))
            print(f"Indexed {count} context chunks.")
        else:
            result = Pipeline(cfg).run(args.prompt)
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print(result["response"])
                if result["contexts"]:
                    print("\nContext references:")
                    for c in result["contexts"]:
                        print(f"[{c['citation']}] {c['source']} (chunk {c['id']})")
                for warning in result["warnings"]:
                    print("Warning: " + warning, file=sys.stderr)
        return 0
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
