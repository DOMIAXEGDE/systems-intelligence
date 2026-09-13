"""P&R command line. The GUI is imported only for the gui subcommand."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
from .canonical import strict_json
from .errors import PandRError, ValidationError

def emit(value):
    print(json.dumps(value,ensure_ascii=True,indent=2))

def build_parser():
    parser=argparse.ArgumentParser(prog='pandr',description='Point & Resolve · horizontal and vertical contact signals')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in ('gui','init','validate','inspect','measure','resolve','transform','render','deliver','export','replay','run'):
        sub=commands.add_parser(name)
        sub.add_argument('--session',default=os.environ.get('PANDR_SESSION','sessions/workspace.json'))
        if name=='init': sub.add_argument('--points',help='JSON point list file')
        if name=='measure':
            sub.add_argument('--plane',choices=['horizontal','vertical','both'],default='both')
            sub.add_argument('--channel',choices=['v','h','both'],default='both')
            sub.add_argument('--policy',default='unique')
        if name=='resolve':
            sub.add_argument('sequence'); sub.add_argument('--commit',action='store_true')
            sub.add_argument('--policy',default='unique'); sub.add_argument('--match',default='all',choices=['all','unique','first_ordered'])
        if name=='transform':
            sub.add_argument('--function'); sub.add_argument('--translate',nargs=2,metavar=('DX','DY'))
            sub.add_argument('--scale',nargs=2,metavar=('SX','SY')); sub.add_argument('--postcompose'); sub.add_argument('--precompose')
        if name=='render':
            sub.add_argument('kind',choices=['text','image','audio','video','code'])
            sub.add_argument('--payload-json'); sub.add_argument('--parameters-json',default='{}')
        if name in ('export','replay'): sub.add_argument('--output',required=True)
        if name=='run': sub.add_argument('script'); sub.add_argument('--timeout',type=int,default=30)
    imported=commands.add_parser('import'); imported.add_argument('bundle'); imported.add_argument('--session',required=True)
    demo=commands.add_parser('demo'); demo.add_argument('--output',default='sessions/demo')
    codec=commands.add_parser('codec'); codec.add_argument('action',choices=['encode','decode','selftest']); codec.add_argument('value',nargs='?')
    codec.add_argument('--alphabet',default=''.join(chr(i) for i in range(32,127)))
    return parser

def main(argv=None):
    args=build_parser().parse_args(argv)
    try:
        from .runtime import Runtime
        if args.command=='gui':
            from .gui import launch
            launch(args.session); return 0
        if args.command=='codec':
            from .codecs import ShortlexCodec
            codec=ShortlexCodec(args.alphabet)
            if args.action=='selftest':
                import unittest
                suite=unittest.defaultTestLoader.discover(str(Path(__file__).resolve().parent.parent/'tests'),pattern='test_codecs.py')
                return 0 if unittest.TextTestRunner(verbosity=1).run(suite).wasSuccessful() else 1
            if args.value is None: raise ValidationError('A codec input is required')
            emit(getattr(codec,args.action)(args.value)); return 0
        if args.command=='demo':
            from .demo import create_demo
            emit(create_demo(args.output)); return 0
        if args.command=='import': emit(Runtime.import_bundle(args.bundle,args.session).validate()); return 0
        if args.command=='init':
            points=strict_json(Path(args.points).read_text(encoding='utf-8')) if args.points else None
            emit(Runtime.create(args.session,points).validate()); return 0
        runtime=Runtime.open(args.session)
        if args.command=='validate': emit(runtime.validate())
        elif args.command=='inspect': emit(runtime.snapshot())
        elif args.command=='measure':
            planes=['horizontal','vertical'] if args.plane=='both' else [args.plane]
            channels=['v','h'] if args.channel=='both' else [args.channel]
            emit({f'{channel}-{plane}':runtime.measure(plane,channel,args.policy) for plane in planes for channel in channels})
        elif args.command=='resolve':
            options={'root_policy':args.policy,'match_policy':args.match}
            emit(runtime.commit(runtime.plan().resolve(args.sequence,**options)) if args.commit else runtime.resolve(args.sequence,**options))
        elif args.command=='transform':
            plan=runtime.plan()
            if args.function: plan.set_function(args.function)
            if args.translate: plan.transform.translate(*args.translate)
            if args.scale: plan.transform.scale(*args.scale)
            if args.precompose: plan.transform.precompose(args.precompose)
            if args.postcompose: plan.transform.postcompose(args.postcompose)
            if not plan.commands: raise ValidationError('Specify a transformation')
            emit(runtime.commit(plan))
        elif args.command=='render':
            plan=runtime.plan()
            if not any(l['type']==args.kind and l['enabled'] for l in runtime.snapshot()['domain']['layers']): plan.add_layer(args.kind)
            payload=strict_json(args.payload_json) if args.payload_json else None
            plan.render(args.kind,payload,strict_json(args.parameters_json)); emit(runtime.commit(plan))
        elif args.command=='deliver': emit(runtime.drain_outbox())
        elif args.command=='export': emit({'bundle':runtime.export_bundle(args.output)})
        elif args.command=='replay': emit(runtime.replay(args.output))
        elif args.command=='run':
            path=Path(args.script).resolve(); source=path.read_text(encoding='utf-8')
            runtime.commit(runtime.plan().save_script(path.stem,source))
            result=runtime.execute_script(path.stem,timeout=args.timeout)
            emit(result); return 0 if result['status']=='success' else 1
        return 0
    except (PandRError,ValueError,OSError,KeyError) as e:
        print(json.dumps({'status':'error','code':getattr(e,'code',type(e).__name__),'message':str(e)}),file=sys.stderr)
        return 1

if __name__=='__main__': raise SystemExit(main())
