"""Service-level observation, independent of browser, CLI and plugin entry points."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, is_dataclass
from functools import wraps
import inspect
import uuid
import time

CURRENT = ContextVar('controller_context', default=None)
OPERATIONS = {}
EVENT_NAMES = '''controller.baseline controller.started controller.shutdown controller.inspect
controller.plot controller.export controller.filter controller.view controller.autonomy
controller.branch controller.branched controller.branch_state
command.requested command.completed command.failed command.indeterminate
context.created context.updated context.export index.committed
chat.created chat.updated chat.turn chat.followup
generator.start generator.batch generator.record generator.progress generator.committed generator.rollback generator.cancel
pipeline.evidence pipeline.selected backend.request backend.response backend.failed backend.fallback
backend.ollama backend.tiny backend.generated_byte backend.output_chunk
training.run training.batch training.step training.evaluation
pandr.baseline pandr.committed pandr.reconciled pandr.execute_script pandr.drain_outbox pandr.branch pandr.script_outcome
skill.state skill.drafted skill.activated skill.disabled skill.granted skill.revoked skill.started skill.finished
skill.cancel skill.delivery_failed skill.proposed skill.approval_started skill.approval skill.approval_interrupted skill.rejected'''.split()


def event_definitions():
    """Discover standard events before their first occurrence, including optional backends."""
    values={name:{'name':name,'source':name.rsplit('.',1)[0],'payload_schema':{'type':['object','array','null']}} for name in set(EVENT_NAMES)|set(OPERATIONS)}
    values['backend.generated_byte']['payload_schema']={'type':'object','properties':{'byte':{'type':'integer','minimum':0,'maximum':255},'index':{'type':'integer','minimum':0},'granularity':{'const':'byte'}},'required':['byte','index','granularity']}
    values['backend.output_chunk']['payload_schema']={'type':'object','properties':{'text':{'type':'string'},'granularity':{'enum':['chunk','complete_response']},'token_detail_available':{'const':False}},'required':['text','granularity','token_detail_available']}
    return values


def serial(value):
    if is_dataclass(value): return asdict(value)
    if isinstance(value, dict): return {str(k): serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [serial(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)): return value
    return str(value)


@contextmanager
def scope(controller, correlation=None, cause=None, depth=0, skill=None):
    previous = CURRENT.get() or {}
    token = CURRENT.set({'controller': controller, 'correlation': correlation or str(uuid.uuid4()),
                         'cause': cause, 'depth': depth, 'skill': skill, 'entities': previous.get('entities',[])})
    try: yield CURRENT.get()
    finally: CURRENT.reset(token)


def emit(name, payload=None, *, entities=None, phase='observed', changes=None):
    context = CURRENT.get()
    if context:
        return context['controller'].emit(name, serial(payload), entities=entities, phase=phase, changes=changes)


def observed(name, source=None):
    def decorate(function):
        OPERATIONS[name] = {'name': name, 'source': f'{function.__module__}.{function.__qualname__}', 'signature': str(inspect.signature(function))}
        @wraps(function)
        def wrapped(*args, **kwargs):
            context = CURRENT.get()
            controller = context['controller'] if context else None
            if controller is None and source is not None:
                from . import get_controller
                value = source(*args, **kwargs)
                if value:
                    controller = value if hasattr(value, 'journal') else get_controller(value if isinstance(value, dict) else {'index_path': str(value)})
            if controller is None: return function(*args, **kwargs)
            def invoke():
                clock = time.monotonic()
                arguments = inspect.signature(function).bind(*args,**kwargs).arguments
                targets=[]
                for key,kind in [('context_id','context'),('session_id','chat')]:
                    if key in arguments: targets.append(f'{kind}:{arguments[key]}')
                CURRENT.get()['entities']=sorted(set(CURRENT.get().get('entities',[])+targets))
                started = emit(name, {'arguments': serial({k:v for k,v in arguments.items() if k!='self'})}, phase='started')
                previous = CURRENT.get()
                with scope(controller, previous['correlation'], started['id'], previous['depth'], previous['skill']):
                    try:
                        result = function(*args, **kwargs)
                        if source is not None: controller.flush()
                        summary = {k:result[k] for k in ('at_sequence','total','next_offset','publication_lag') if k in result} if name in ('controller.inspect','controller.plot') else serial(result)
                        emit(name, {'result': summary, 'duration_seconds': time.monotonic()-clock}, phase='completed')
                        return result
                    except Exception as exc:
                        emit(name, {'error': str(exc), 'error_type': type(exc).__name__}, phase='failed')
                        raise
            if context: return invoke()
            with scope(controller): return invoke()
        return wrapped
    return decorate


def initialize_outbox(db):
    db.execute('CREATE TABLE IF NOT EXISTS controller_outbox (ordinal INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, event TEXT NOT NULL, published INTEGER NOT NULL DEFAULT 0)')


def outbox(db, name, kind, identifier, state, *, extra_changes=None):
    """Called inside the same transaction as the mutation it describes."""
    import json
    from datetime import datetime, timezone
    context = CURRENT.get() or {}
    event = {'type': name, 'phase': 'committed', 'entities': [f'{kind}:{identifier}'],
             'changes': [{'kind': kind, 'id': str(identifier), 'state': state}] + (extra_changes or []),
             'correlation': context.get('correlation'), 'cause': context.get('cause'),
             'depth': context.get('depth', 0), 'skill': context.get('skill'),
             'timestamp':datetime.now(timezone.utc).isoformat()}
    initialize_outbox(db)
    db.execute('INSERT INTO controller_outbox(id,event) VALUES (?,?)', (str(uuid.uuid4()), json.dumps(event, ensure_ascii=False, allow_nan=False)))
