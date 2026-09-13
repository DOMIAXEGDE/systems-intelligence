"""Controller orchestration, historical projections and shared P&R operations."""
from contextlib import contextmanager
import inspect
import json
from pathlib import Path
import sqlite3
import threading
import uuid

import fastjsonschema

from . import models
from .hooks import CURRENT, OPERATIONS, initialize_outbox, scope, serial, observed, event_definitions
from .journal import Journal


class GenerationCancellation:
    """Local fast cancellation plus a durable flag shared with CLI processes."""
    def __init__(self,journal): self.journal=journal; self.local=threading.Event()
    def set(self): self.local.set(); self.journal.set_setting('generation_cancelled',True)
    def clear(self): self.local.clear(); self.journal.set_setting('generation_cancelled',False)
    def is_set(self): return self.local.is_set() or self.journal.setting('generation_cancelled',False)


class Controller:
    def __init__(self, config):
        from ..core import DEFAULTS
        self.config = DEFAULTS | config
        self.database = Path(self.config['index_path']).resolve()
        self.directory = self.database.parent / (self.database.stem + '-controller')
        self.journal = Journal(self.directory / 'journal.sqlite3')
        self.closed = False
        self._lock = threading.RLock()
        self._relay_lock = threading.RLock()
        self._pandr = None
        self._skills = None
        self._commands = {}
        self._handlers = {}
        self.cancel = GenerationCancellation(self.journal)
        self._register_defaults()
        self._baseline()
        self.flush()

    @contextmanager
    def app_db(self):
        db = sqlite3.connect(self.database, timeout=30)
        try:
            with db: yield db
        finally: db.close()

    @property
    def pandr(self):
        with self._lock:
            if self._pandr is None:
                from pandr import Runtime
                from pandr.repository import FileLock
                with FileLock(self.directory/'pandr-init'):
                    path = self.directory / 'pandr' / 'controller.json'
                    self._pandr = Runtime.open(path) if path.exists() else Runtime.create(path)
                    if not any(v['kind'] == 'pandr' for v in self.journal.state(self.journal.head())):
                        self.emit('pandr.baseline', phase='baseline', changes=[{'kind': 'pandr', 'id': 'main', 'state': self._pandr.snapshot()}])
            return self._pandr

    @property
    def skills(self):
        with self._lock:
            if self._skills is None:
                from .skills import Skills
                self._skills = Skills(self)
            return self._skills

    def emit(self, name, payload=None, *, entities=None, phase='observed', changes=None, _records=None):
        current = CURRENT.get() or {}
        event = self.journal.append(name, payload, entities=sorted(set((entities or [])+current.get('entities',[]))), phase=phase, changes=changes,
                                    correlation=current.get('correlation'), cause=current.get('cause'),
                                    depth=current.get('depth', 0), skill=current.get('skill'), _records=_records)
        if self._skills: self._skills.notify(event)
        return event

    def _baseline(self):
        if self.journal.setting('baseline_complete'): return
        changes = []
        if self.database.is_file():
            with self.app_db() as db:
                db.row_factory = sqlite3.Row
                initialize_outbox(db)
                tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table, kind, key in [('managed_contexts', 'context', 'id'), ('chat_sessions', 'chat', 'id'), ('chat_turns', 'turn', None)]:
                    if table not in tables: continue
                    for row in db.execute(f'SELECT * FROM {table}'):
                        value = self._row(table, dict(row))
                        identifier = value[key] if key else f"{value['session_id']}:{value['number']}"
                        changes.append({'kind': kind, 'id': identifier, 'state': value})
                if 'chunks' in tables:
                    changes.append({'kind': 'index', 'id': 'main', 'state': {'chunks': [dict(r) for r in db.execute('SELECT rowid,source,text FROM chunks')]}})
        self.journal.append('controller.baseline', {'history': 'Imported current state; prior events are unavailable'},
                            identifier='migration-baseline-v1', phase='baseline', changes=changes)
        self.journal.set_setting('baseline_complete', True)

    @staticmethod
    def _row(table, row):
        if table == 'managed_contexts':
            row['enabled'] = bool(row['enabled']); row['provenance'] = json.loads(row['provenance'])
        elif table == 'chat_turns': row['result'] = json.loads(row['result'])
        return row

    def flush(self):
        if not self.database.is_file(): return 0
        from pandr.repository import FileLock
        with self._relay_lock, FileLock(self.directory/'outbox-relay'):
            with self.app_db() as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='controller_outbox'").fetchone(): return 0
                rows = db.execute('SELECT ordinal,id,event FROM controller_outbox WHERE published=0 ORDER BY ordinal').fetchall()
            for ordinal, identifier, raw in rows:
                value = json.loads(raw)
                event = self.journal.append(value.pop('type'), identifier=identifier, **value)
                with self.app_db() as db:
                    db.execute('UPDATE controller_outbox SET published=1 WHERE ordinal=?', (ordinal,))
                if self._skills: self._skills.notify(event)
            return len(rows)

    def publication_lag(self):
        if not self.database.is_file(): return 0
        with self.app_db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='controller_outbox'").fetchone(): return 0
            return db.execute('SELECT count(*) FROM controller_outbox WHERE published=0').fetchone()[0]

    def register(self, name, handler, *, capability=None, input_schema=None, output_schema=None,
                 mutating=False, revision=False, external=False):
        if name in self._commands: raise ValueError(f'Duplicate command: {name}')
        schema = input_schema or {'type': 'object'}
        self._commands[name] = {'name': name, 'input_schema': schema, 'output_schema': output_schema or {},
                                'capability': capability or name, 'mutating': mutating, 'revision_required': revision,
                                'external_effects': external, 'idempotency': 'request ID; uncertain effects require reconciliation'}
        self._handlers[name] = (handler, fastjsonschema.compile(schema))

    def _register_defaults(self):
        from ..context_store import ContextStore
        from ..chat import ChatService
        from ..core import Pipeline, SQLiteRetriever
        from ..generator import Recipe
        def store(): return ContextStore(self.database)
        def chat(): return ChatService(store(), self.config)
        def bind(name, handler, properties=(), required=(), **options):
            types = {'id':'string','title':'string','body':'string','tags':'string','enabled':'boolean',
                     'revision':'integer','prompt':'string','request_id':'string','query':'string',
                     'status':'string','page':'integer','page_size':'integer','directory':'string',
                     'recipe':'object','local_only':'boolean','offset':'integer','limit':'integer',
                     'include_models':'boolean','commands':'array','resolution':'integer','name':'string',
                     'definition':'object','action':'string','identifier':'string','options':'object',
                     'destination':'string','format':'string','digest':'string','paused':'boolean'}
            props = {key: {'type':types[key]} if key in types else {} for key in properties}
            output_type='array' if name in ('pandr.deliver','pandr.measure','pandr.resolve') else 'string' if name=='pandr.export' else 'object'
            self.register(name, handler, input_schema={'type': 'object', 'properties': props,
                                                       'required': list(required), 'additionalProperties': False}, output_schema={'type':output_type}, **options)
        bind('context.append', lambda p: store().append(p), ('title','body','enabled','tags'), ('title','body'), mutating=True)
        bind('context.get', lambda p: store().get(p['id']), ('id',), ('id',))
        bind('context.list', lambda p: store().list(**p), ('query','status','page','page_size'))
        bind('context.edit', lambda p: store().edit(p['id'], p['revision'], p), ('id','revision','title','body','enabled','tags'), ('id','revision','title','body'), mutating=True, revision=True)
        bind('context.toggle', lambda p: store().toggle(p['id'], p['revision'], p['enabled']), ('id','revision','enabled'), ('id','revision','enabled'), mutating=True, revision=True)
        bind('context.adopt', lambda p: {'added': store().adopt_files(p.get('directory', self.config['context_dir']))}, ('directory',), mutating=True)
        bind('context.export', lambda p: {'jsonl': ''.join(store().export_jsonl())})
        bind('index.build', lambda p: {'chunks': SQLiteRetriever(self.database).build(Path(p.get('directory', self.config['context_dir'])))}, ('directory',), mutating=True)
        bind('chat.create', lambda p: chat().create(), mutating=True)
        bind('chat.list', lambda p: chat().list())
        bind('chat.get', lambda p: chat().get(p['id']), ('id',), ('id',))
        bind('chat.send', lambda p: chat().send(p['id'], p['prompt'], p['revision'], p['request_id']), ('id','prompt','revision','request_id'), ('id','prompt','revision','request_id'), mutating=True, revision=True, external=True)
        bind('pipeline.run', lambda p: Pipeline(self.config | ({'backend': 'extractive'} if p.get('local_only') else {})).run(p['prompt']), ('prompt','local_only'), ('prompt',), external=True)
        bind('generator.plan', lambda p: Recipe.from_dict(p['recipe']).plan(), ('recipe',), ('recipe',))
        bind('generator.preview', lambda p: Recipe.from_dict(p['recipe']).preview(), ('recipe',), ('recipe',))
        bind('generator.generate', lambda p: self.generate(Recipe.from_dict(p['recipe'])), ('recipe',), ('recipe',), mutating=True)
        bind('generator.cancel', lambda p: self.cancel_generation(), mutating=True)
        bind('controller.inspect', lambda p: self.inspect(**p), ('selection','at_sequence','offset','limit','include_models'))
        bind('controller.events', lambda p: self.events(**p), ('selection','after_sequence','limit','until'))
        bind('controller.reconcile', lambda p: self.reconcile(p['request_id'],p['command']), ('request_id','command'), ('request_id','command'))
        bind('controller.plot', lambda p: self.plot(**p), ('selection','at_sequence','view','offset','limit'))
        bind('controller.export', lambda p: self.export_replay(**p), ('selection','start_sequence','end_sequence','format','view','stride','fps','include_lifecycle','max_frames'))
        bind('controller.branch', lambda p: self.branch(**p), ('at_sequence','destination'), ('at_sequence','destination'), mutating=True, external=True)
        bind('controller.filter.save', lambda p: self.save_filter(p['name'], p['selection']), ('name','selection'), ('name','selection'), mutating=True)
        bind('controller.view.publish', self.publish_view, ('name','definition'), ('name','definition'), mutating=True)
        bind('pandr.inspect', lambda p: self.pandr.snapshot())
        bind('pandr.measure', lambda p: self.pandr.measure(**p), ('plane','channel','policy','branch'))
        bind('pandr.resolve', lambda p: self.pandr.resolve(p['sequence'], **p.get('options', {})), ('sequence','options'), ('sequence',))
        bind('pandr.commit', self._pandr_commit, ('commands','revision'), ('commands','revision'), mutating=True, revision=True)
        bind('pandr.plot', self._pandr_plot, ('functions','x_range','y_range','resolution'))
        bind('pandr.codec', self._codec, ('action','value','alphabet'), ('action','value'))
        bind('pandr.alphabet', self._alphabet, ('action','options'), ('action',))
        bind('pandr.execute', lambda p: self._pandr_effect('execute_script', **p), ('identifier','timeout'), ('identifier',), mutating=True, external=True)
        bind('pandr.deliver', lambda p: self._pandr_effect('drain_outbox'), mutating=True, external=True)
        bind('pandr.export', lambda p: self._pandr_export())
        bind('pandr.replay', lambda p: self.pandr.replay(self.directory / 'exports' / str(uuid.uuid4()) / 'replay.json'))
        bind('skill.list', lambda p: self.skills.list())
        bind('skill.draft', lambda p: self.skills.draft(**p), ('manifest','author'), ('manifest',), mutating=True)
        bind('skill.activate', lambda p: self.skills.activate(p['id'],p['digest']), ('id','digest'), ('id','digest'), mutating=True)
        bind('skill.grant', lambda p: self.skills.grant(p['id'],p['digest'],p['capabilities'],p['scope'],p.get('budgets')), ('id','digest','capabilities','scope','budgets'), ('id','digest','capabilities','scope'), mutating=True)
        for action in ('disable','revoke','cancel'):
            bind('skill.'+action, lambda p,a=action: getattr(self.skills,a)(p['id']), ('id',), ('id',), mutating=True)
        bind('skill.run', lambda p: self.skills.run(p['id'],p.get('input',{})), ('id','input'), ('id',), mutating=True, external=True)
        bind('skill.pause', lambda p: self.skills.pause(p['paused']), ('paused',), ('paused',), mutating=True)
        bind('proposal.approve', lambda p: self.skills.approve(p['id'],p['digest']), ('id','digest'), ('id','digest'), mutating=True, external=True)
        bind('proposal.reject', lambda p: self.skills.reject(p['id']), ('id',), ('id',), mutating=True)
        bind('training.run', self.train, ('data','validation','preset','steps','batch_size','learning_rate','device','seed','threads','output'), ('data','validation'), mutating=True, external=True)

    def train(self,payload):
        from argparse import Namespace
        from ..train import train
        options={'preset':'micro','steps':200,'batch_size':2,'learning_rate':3e-4,'device':'cpu','seed':42,'threads':2,
                 'output':str(self.directory/'training'/'tiny.pt')} | payload
        for key in ('data','validation','output'): options[key]=Path(options[key])
        if min(options['steps'],options['batch_size'],options['threads'],options['learning_rate'])<=0: raise ValueError('Training parameters must be positive')
        train(Namespace(**options),self.config)
        return {'checkpoint':str(options['output']),'metrics':str(options['output'].with_suffix('.metrics.json'))}

    def registry(self):
        from pandr.contracts import COMMANDS
        from pandr.runtime import Plan
        self.skills  # Restore approved namespaced extensions after a service restart.
        with self.journal.connect() as db:
            event_types = [models.loads(r[0]) for r in db.execute('SELECT schema FROM event_types ORDER BY name')]
        catalog={v['name']:v for v in event_types}; catalog.update(event_definitions())
        return {'schema': 'controller-registry/1', 'commands': list(self._commands.values()),
                'events': [catalog[name] for name in sorted(catalog)], 'operations': list(OPERATIONS.values()), 'views': self.view_definitions(),
                'pandr_plan_commands': sorted(COMMANDS),
                'pandr_plan_api': {name:str(inspect.signature(fn)) for name,fn in inspect.getmembers(Plan,inspect.isfunction) if not name.startswith('_')},
                'pandr_utilities': ['expressions','geometry','sequences','contracts','alphabets','codecs','artifacts','execution','runtime','plotting'],
                'plugin_trust': 'Trusted local Python; controller capabilities are not an OS sandbox'}

    def dispatch(self, command, payload, request_id, expected_revision=None):
        if self.closed: raise RuntimeError('Controller is closed')
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 160: raise ValueError('A request ID is required')
        if command not in self._commands: raise ValueError(f'Unknown command: {command}')
        payload = dict(payload)
        if expected_revision is not None:
            if 'revision' in payload and payload['revision'] != expected_revision: raise ValueError('Conflicting revisions')
            payload['revision'] = expected_revision
        handler, validate = self._handlers[command]
        validate(payload)
        fingerprint = models.sha(models.dumps([command, payload]).encode())
        with self.journal.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT * FROM requests WHERE id=?', (request_id,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint: raise ValueError('Request ID already belongs to another command')
                if old['status'] == 'completed': return models.loads(old['result'])
                if old['status'] == 'failed': raise RuntimeError(models.loads(old['result'])['error'])
                # Never repeat an operation whose process died after starting.
            else:
                db.execute('INSERT INTO requests VALUES (?,?,?,NULL)', (request_id, fingerprint, 'running'))
        if old: return self.reconcile(request_id, command)
        prior = CURRENT.get() or {}
        with scope(self, request_id, prior.get('cause'), prior.get('depth', 0), prior.get('skill')):
            CURRENT.get()['entities']=self.targets(command,payload)
            self.emit('command.requested', {'command': command, 'payload': payload}, phase='started', entities=self.targets(command, payload))
            try:
                result = serial(handler(payload))
                self.flush()
                fastjsonschema.validate(self._commands[command]['output_schema'], result)
                with self.journal.connect() as db:
                    db.execute('UPDATE requests SET status=?,result=? WHERE id=?', ('completed', models.dumps(result), request_id))
                self.emit('command.completed', {'command': command, 'result': result}, phase='completed', entities=self.targets(command, payload))
                return result
            except Exception as exc:
                uncertain=(self._commands[command]['external_effects'] or command=='pandr.commit') and not isinstance(exc,ValueError)
                status = 'indeterminate' if uncertain or self._has_committed_request(request_id) else 'failed'
                with self.journal.connect() as db:
                    db.execute('UPDATE requests SET status=?,result=? WHERE id=?', (status, models.dumps({'error': str(exc)}), request_id))
                self.emit('command.'+status, {'command': command, 'error': str(exc)}, phase=status)
                raise

    def _has_committed_request(self, request_id):
        if not self.database.is_file(): return False
        with self.app_db() as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='controller_outbox'").fetchone(): return False
            return any(json.loads(row[0]).get('correlation') == request_id for row in db.execute('SELECT event FROM controller_outbox'))

    def reconcile(self, request_id, command):
        # Read-only receipt reconciliation: it never invokes the handler.
        self.flush()
        committed = [e for e in self.journal.events() if e['correlation'] == request_id and e['changes'] and e['phase']=='committed']
        if command == 'pandr.commit':
            record = self.pandr.snapshot().get('request_ledger', {}).get(request_id)
            if record:
                self.emit('pandr.reconciled', phase='committed', changes=[{'kind':'pandr','id':'main','state':self.pandr.snapshot()}])
                return {'status': 'committed', 'receipt': record['receipt']}
        return {'status': 'committed_pending_result' if committed else 'indeterminate', 'request_id': request_id,
                'events': [e['seq'] for e in committed], 'message': 'No action was repeated. Inspect the recorded outcome before issuing a new request.'}

    @staticmethod
    def targets(command, payload):
        kind = command.split('.')[0]
        if kind == 'pandr': return ['pandr:main']
        return [f"{kind}:{payload['id']}"] if 'id' in payload else [kind+':*']

    def _pandr_commit(self, payload):
        from pandr.contracts import COMMANDS
        from pandr import Function
        request_id = CURRENT.get()['correlation']
        plan = self.pandr.plan(expected_revision=payload['revision'], request_id=request_id)
        for command in payload['commands']:
            op = command['op']
            if op not in COMMANDS: raise ValueError('Unknown P&R command')
            command = dict(command)
            if op in ('set_function','precompose','postcompose') and isinstance(command.get('function'),str):
                command['function'] = Function.parse(command['function']).to_dict()
            plan._add(command.pop('op'), **command)
        result = self.pandr.commit(plan)
        self.emit('pandr.committed', {'receipt':result}, phase='committed', changes=[{'kind':'pandr','id':'main','state':self.pandr.snapshot()}])
        return result

    def _pandr_plot(self, payload):
        result = self.pandr.plot(**payload)
        return {'segments':result.segments,'labels':result.labels,'x_range':result.x_range,'y_range':result.y_range,'diagnostics':result.diagnostics}

    def _pandr_effect(self, method, **parameters):
        result = getattr(self.pandr, method)(**parameters)
        self.emit('pandr.'+method, result, phase='committed', changes=[{'kind':'pandr','id':'main','state':self.pandr.snapshot()}])
        return result

    @staticmethod
    def _codec(payload):
        from pandr.codecs import ShortlexCodec
        if payload['action'] not in ('encode','decode'): raise ValueError('Codec action must be encode or decode')
        codec = ShortlexCodec(**({'alphabet':payload['alphabet']} if 'alphabet' in payload else {}))
        return {'value': getattr(codec, payload['action'])(payload['value'])}

    @staticmethod
    def _alphabet(payload):
        from pandr.alphabets import create_alphabet, edit_glyph, validate_alphabet
        functions = {'create':create_alphabet,'edit':edit_glyph,'validate':validate_alphabet}
        if payload['action'] not in functions: raise ValueError('Unknown alphabet action')
        return functions[payload['action']](**payload.get('options', {}))

    def generate(self, recipe, progress=None):
        from ..context_store import ContextStore
        from pandr.repository import FileLock
        with FileLock(self.directory/'generation',timeout=0):
            self.cancel.clear()
            return ContextStore(self.database).append_generated(recipe, self.cancel, progress)

    def cancel_generation(self):
        self.cancel.set(); self.emit('generator.cancel', phase='requested')
        return {'status':'cancellation_requested'}

    def save_filter(self, name, selection):
        if not isinstance(name,str) or not name.strip() or len(name)>160: raise ValueError('Filter name must have 1–160 characters')
        self._selection(selection)
        self.emit('controller.filter',{'name':name,'selection':selection},phase='committed',changes=[{'kind':'filter','id':name,'state':selection}],
                  _records=[{'table':'settings','id':'filter:'+name,'state':selection}])
        return {'name':name,'selection':selection}

    def publish_view(self, payload):
        value=payload['definition']
        if not isinstance(value,dict) or not isinstance(value.get('series'),list): raise ValueError('View definition requires series')
        if not isinstance(value.get('source_fields'),list) or not isinstance(value.get('units'),str): raise ValueError('View definition requires source_fields and units')
        if len(models.dumps(value))>2000000: raise ValueError('View exceeds 2 MB')
        for series in value['series']:
            if not isinstance(series.get('points'),list) or any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) for v in p) for p in series['points']): raise ValueError('View points require numeric x/y pairs')
        self.emit('controller.view',payload,phase='committed',changes=[{'kind':'view','id':payload['name'],'state':value}])
        return {'name':payload['name'],'status':'published'}

    def _selection(self, selection):
        if selection is None: return {}
        if isinstance(selection, str): return {'ids':[selection]}
        if isinstance(selection, list): return {'ids':selection}
        if not isinstance(selection, dict): raise ValueError('Invalid selection')
        if 'saved_filter' in selection:
            result = self.journal.setting('filter:'+selection['saved_filter'])
            if result is None: raise ValueError('Unknown saved filter')
            return self._selection(result)
        return selection

    @staticmethod
    def _match(entity, selection):
        from fnmatch import fnmatchcase
        ids = selection.get('ids')
        key = f"{entity['kind']}:{entity['id']}"
        if ids and not any(fnmatchcase(key,i) or entity['id']==i for i in ids):
            if entity['kind'] != 'turn' or 'chat:'+entity['state'].get('session_id','') not in ids: return False
        if selection.get('kinds') and entity['kind'] not in selection['kinds']: return False
        state = entity['state']
        if 'enabled' in selection and state.get('enabled') != selection['enabled']: return False
        if selection.get('query') and selection['query'].casefold() not in json.dumps(state,ensure_ascii=False).casefold(): return False
        if selection.get('tags') and not all(t in state.get('tags','').split() for t in selection['tags']): return False
        return True

    def _at(self, sequence):
        sequence = self.journal.head() if sequence is None else sequence
        if type(sequence) is not int or not 1 <= sequence <= self.journal.head(): raise ValueError('Invalid journal position')
        return sequence

    @observed('controller.inspect', lambda self, *a, **kw: self)
    def inspect(self, selection=None, at_sequence=None, offset=0, limit=100, include_models=False):
        return self._inspect(selection,at_sequence,offset,limit,include_models)

    def _inspect(self, selection=None, at_sequence=None, offset=0, limit=100, include_models=False):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 1000: raise ValueError('Invalid inspection page')
        if at_sequence is None: self.flush()
        sequence = self._at(at_sequence)
        selection = self._selection(selection)
        values = self.journal.state(sequence)
        if selection.get('correlation'):
            keys = {key for e in self.journal.events(until=sequence) if e['correlation']==selection['correlation'] for key in e['entities']}
            selection = dict(selection, ids=sorted(keys))
            if not keys: values = []
        selected = [v for v in values if v['state'] is not None and self._match(v,selection)]
        page = selected[offset:offset+limit]
        if include_models:
            page = [v | {'function_model':self.journal.model(v['model'])} for v in page]
        return {'schema':'controller-inspection/1','at_sequence':sequence,'publication_lag':self.publication_lag(),
                'entities':page,'total':len(selected),'next_offset':offset+limit if offset+limit<len(selected) else None}

    def events(self, selection=None, after_sequence=0, limit=100, until=None):
        if type(limit) is not int or not 1 <= limit <= 1000: raise ValueError('Invalid event page')
        if after_sequence is None: after_sequence=0
        if type(after_sequence) is not int or after_sequence<0: raise ValueError('Invalid event cursor')
        selection = self._selection(selection); end = self._at(until)
        from fnmatch import fnmatchcase
        patterns=selection.get('ids',[])
        if set(selection)-{'ids','correlation'}:
            patterns=[f"{v['kind']}:{v['id']}" for v in self.journal.state(end) if v['state'] is not None and self._match(v,selection)]
        result = []; scanned = after_sequence
        for event in self.journal.events(after_sequence,end):
            scanned = event['seq']
            if selection.get('correlation') and event['correlation'] != selection['correlation']: continue
            targets=event['entities'] + ['chat:'+key[5:].rsplit(':',1)[0] for key in event['entities'] if key.startswith('turn:')]
            if patterns and not any(fnmatchcase(key,pattern) or fnmatchcase(pattern,key) or key.partition(':')[2]==pattern for key in targets for pattern in patterns): continue
            if not patterns and set(selection)-{'correlation'}: continue
            result.append(event)
            if len(result) >= limit: break
        return {'events':result,'next_sequence':scanned if scanned<end else None,'at_sequence':end}

    def view_definitions(self, at_sequence=None):
        result = [
            {'id':'bytes','field':'typed state bytes','units':'byte value (0–255)','recoverable':True},
            {'id':'context_size','field':'context.body','units':'UTF-8 bytes'},
            {'id':'revision','field':'state.revision','units':'revision'},
            {'id':'enabled','field':'context.enabled','units':'0/1'},
            {'id':'generation','field':'generator.processed / count','units':'records'},
            {'id':'chat_activity','field':'turn.number','units':'turn count'},
            {'id':'retrieval','field':'contexts[].retrieval_score','units':'retrieval score'},
            {'id':'candidates','field':'candidates[].score.value','units':'lexical score, not confidence'},
            {'id':'latency','field':'duration_seconds','units':'seconds'},
            {'id':'outcomes','field':'event.phase','units':'categorical outcome'}]
        for entity in self.journal.state(self._at(at_sequence)):
            if entity['kind']=='view' and entity['state'] is not None:
                result.append({'id':'view:'+entity['id'],'field':entity['state'].get('source_fields',[]),'units':entity['state'].get('units','declared by author'),'recoverable':False})
        return result

    def _pandr_export(self):
        exports=self.directory/'exports'; exports.mkdir(parents=True,exist_ok=True)
        return self.pandr.export_bundle(exports/(str(uuid.uuid4())+'.pandr'))

    @observed('controller.plot', lambda self, *a, **kw: self)
    def plot(self, selection=None, at_sequence=None, view='bytes', offset=0, limit=100):
        from .visuals import plot_state
        return plot_state(self, selection, at_sequence, view, offset, limit)

    def replay(self, selection, start_sequence, end_sequence):
        from .visuals import all_entities
        start, end = self._at(start_sequence), self._at(end_sequence)
        if start>end: raise ValueError('Replay range is reversed')
        def frame(seq):
            entities=all_entities(self,selection,seq)
            return {'at_sequence':seq,'entities':entities,'total':len(entities),'next_offset':None}
        current=frame(start); yield current
        for event in self.journal.events(start,end):
            if event['phase'] in ('baseline','committed') and event['changes']:
                following=frame(event['seq'])
                if following['entities']!=current['entities']:
                    yield following; current=following

    def export_replay(self, selection=None, start_sequence=1, end_sequence=None, format='gif', **options):
        from .visuals import export_replay
        return export_replay(self, selection, start_sequence, self._at(end_sequence), format, **options)

    def branch(self, at_sequence, destination):
        from .visuals import branch
        return branch(self,self._at(at_sequence),destination)

    def close(self):
        if self.closed: return
        self.cancel.local.set()
        if self._skills: self._skills.close()
        if self._pandr: self._pandr.cancel_scripts()
        self.flush(); self.emit('controller.shutdown',phase='completed'); self.closed=True

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
