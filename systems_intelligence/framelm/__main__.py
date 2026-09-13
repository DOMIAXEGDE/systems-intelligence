import argparse
import json
import sys
import uuid
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
    controller = sub.add_parser('controller', help='Inspect or operate the P&R controller')
    controller.add_argument('operation', choices=['registry','inspect','events','dispatch','export','branch','skills'])
    controller.add_argument('--data', default='{}', help='JSON operation arguments')
    ask = sub.add_parser("ask")
    ask.add_argument("prompt")
    ask.add_argument("--json", action="store_true", help="Show the frame, contexts, candidates and score")
    args = parser.parse_args()
    active_controller=None
    try:
        cfg = load_config(args.config)
        if args.command!='studio':
            from .controller import get_controller
            active_controller=get_controller(cfg)
        if args.command == 'controller':
            from .controller import get_controller
            from .controller.web import post
            c=active_controller
            data=json.loads(args.data)
            if args.operation=='registry': result=c.registry()
            elif args.operation=='inspect': result=c.inspect(**data)
            elif args.operation=='events': result=c.events(**data)
            elif args.operation=='skills': result=c.skills.list()
            else: result=post(c,args.operation,data)
            print(json.dumps(result,ensure_ascii=False,indent=2))
        elif args.command == "studio":
            from .studio import serve
            serve(cfg, args.port, not args.no_browser)
        elif args.command == "generate":
            from .generator import Recipe
            from .context_store import ContextStore
            recipe = Recipe.from_dict(json.loads(args.recipe.read_text(encoding="utf-8")))
            from .controller import get_controller
            c=active_controller
            result=c.dispatch('generator.generate' if args.execute else 'generator.preview', {'recipe':recipe.__dict__},str(uuid.uuid4()))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.command == "index":
            count = active_controller.dispatch('index.build',{},str(uuid.uuid4()))['chunks']
            print(f"Indexed {count} context chunks.")
        else:
            result = active_controller.dispatch('pipeline.run',{'prompt':args.prompt},str(uuid.uuid4()))
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
    finally:
        if active_controller: active_controller.close()


if __name__ == "__main__":
    raise SystemExit(main())
