"""Version-approved trusted Python skills with supervised command proposals."""
from concurrent.futures import ThreadPoolExecutor
from fnmatch import fnmatchcase
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import json
from pathlib import Path
import queue
import re
import secrets
import threading
import uuid

import fastjsonschema
from pandr.execution import ExecutionService
from pandr.repository import FileLock
from . import models
from .hooks import scope, CURRENT

DEFAULT_BUDGETS={'timeout':30,'max_output':262144,'max_actions':10,'max_depth':8}


def execution_budgets(values=None):
    limits=DEFAULT_BUDGETS | (values or {})
    if set(limits)!=set(DEFAULT_BUDGETS) or any(type(v) is not int or v<1 for v in limits.values()): raise ValueError('Invalid skill budgets')
    if limits['timeout']>86400 or limits['max_output']>4194304 or limits['max_actions']>1000 or limits['max_depth']>64: raise ValueError('Skill budgets exceed runner limits')
    return limits


class Skills:
    def __init__(self, controller):
        self.controller=controller
        self.journal=controller.journal
        self.queue=queue.Queue()
        self.stop=threading.Event()
        self.running={}
        self.lock=threading.RLock()
        self.worker=None
        self.service_lock=None
        self.broker=None
        self.tokens={}
        self.journal.set_setting('autonomy_paused',self.journal.setting('autonomy_paused',False))
        self._recover_approvals()
        self._restore_extensions()

    def _get(self, identifier):
        with self.journal.connect() as db:
            row=db.execute('SELECT data FROM skills WHERE id=?',(identifier,)).fetchone()
            if not row: raise ValueError('Unknown skill')
            return models.loads(row[0])

    def _save(self, skill):
        self.controller.emit('skill.state',{'id':skill['id'],'digest':skill['digest']},phase='committed',changes=[{'kind':'skill','id':skill['id'],'state':skill}],
                             _records=[{'table':'skills','id':skill['id'],'state':skill}])

    def _proposal(self,identifier):
        with self.journal.connect() as db:
            row=db.execute('SELECT data FROM proposals WHERE id=?',(identifier,)).fetchone()
            if not row: raise ValueError('Unknown action proposal')
            return models.loads(row[0])

    def _proposal_lock(self,identifier): return FileLock(self.controller.directory/('approval-'+str(uuid.UUID(identifier))),timeout=0)

    def _record_proposal(self,name,proposal,phase):
        return self.controller.emit(name,proposal,phase=phase,entities=proposal['targets'],
                                    changes=[{'kind':'proposal','id':proposal['id'],'state':proposal}],
                                    _records=[{'table':'proposals','id':proposal['id'],'state':proposal}])

    def _recover_approvals(self):
        from pandr.errors import RevisionConflict
        with self.journal.connect() as db: proposals=[models.loads(r[0]) for r in db.execute('SELECT data FROM proposals')]
        for proposal in proposals:
            if proposal['status']!='executing': continue
            try:
                with self._proposal_lock(proposal['id']):
                    proposal=self._proposal(proposal['id'])
                    if proposal['status']=='executing':
                        proposal.update(status='indeterminate',error='Approval execution was interrupted. Reconcile its request ID; no action was repeated.')
                        self._record_proposal('skill.approval_interrupted',proposal,'indeterminate')
            except RevisionConflict: pass  # Another process is still executing this approval.

    def _items(self):
        with self.journal.connect() as db: return [models.loads(r[0]) for r in db.execute('SELECT data FROM skills ORDER BY id')]

    def list(self):
        with self.journal.connect() as db:
            return {'skills':[models.loads(r[0]) for r in db.execute('SELECT data FROM skills ORDER BY id')],
                    'proposals':[models.loads(r[0]) for r in db.execute('SELECT data FROM proposals ORDER BY rowid DESC')],
                    'paused':self.journal.setting('autonomy_paused',False),'running':list(self.running)}

    def draft(self, manifest, author='human'):
        if isinstance(manifest,str):
            path=Path(manifest).resolve(); manifest=json.loads(path.read_text(encoding='utf-8'))
            for skill in manifest.get('skills',[]):
                if 'entrypoint' in skill:
                    entry=(path.parent/skill['entrypoint']).resolve()
                    if not entry.is_relative_to(path.parent): raise ValueError('Plugin entrypoint escapes its package')
                    skill['source']=entry.read_text(encoding='utf-8')
        if not isinstance(manifest,dict) or manifest.get('api_version')!=1: raise ValueError('Plugin requires api_version 1')
        name=manifest.get('id','')
        if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',name): raise ValueError('Invalid plugin ID')
        if not isinstance(manifest.get('version'),str) or not manifest['version']: raise ValueError('Plugin version required')
        if not isinstance(manifest.get('skills'),list) or not manifest['skills']: raise ValueError('Plugin requires skills')
        for dependency in manifest.get('dependencies',[]):
            # Installation is explicit; drafting never runs pip or imports plugin code.
            from packaging.requirements import Requirement
            requirement=Requirement(dependency)
            try: installed=importlib.metadata.version(requirement.name)
            except importlib.metadata.PackageNotFoundError as exc: raise ValueError(f'Missing plugin dependency: {dependency}') from exc
            if installed not in requirement.specifier: raise ValueError(f'Incompatible plugin dependency: {dependency}')
        result=[]
        for original in manifest['skills']:
            item=dict(original); short=item.get('id','')
            if not re.fullmatch(r'[a-z][a-z0-9_-]{0,63}',short): raise ValueError('Invalid skill ID')
            source=item.get('source')
            if not isinstance(source,str) or not source.strip() or len(source.encode())>1048576: raise ValueError('Skill source required, maximum 1 MiB')
            compile(source,'<skill>','exec')
            for key in ('capabilities','scope','triggers'):
                if not isinstance(item.get(key,[]),list) or not all(isinstance(v,str) for v in item.get(key,[])): raise ValueError(f'{key} must be a string list')
            item.update(id=name+'.'+short,plugin=name,plugin_version=manifest['version'],api_version=1,
                        dependencies=manifest.get('dependencies',[]),author=author,event_schemas=manifest.get('events',{}))
            if not isinstance(item['event_schemas'],dict): raise ValueError('Plugin events must map names to JSON schemas')
            for event_name,event_schema in item['event_schemas'].items():
                if not event_name.startswith('plugin.'+name+'.'): raise ValueError('Plugin events must use their namespace')
                fastjsonschema.compile(event_schema)
            item.setdefault('capabilities',[]); item.setdefault('scope',[]); item.setdefault('triggers',[])
            item.setdefault('instructions',''); item.setdefault('input_schema',{'type':'object'}); item.setdefault('output_schema',{})
            item['budgets']=execution_budgets(item.get('budgets'))
            fastjsonschema.compile(item['input_schema']); fastjsonschema.compile(item['output_schema'])
            content_digest=models.sha(models.dumps(item).encode())
            item.update(digest=content_digest,approved_digest=None,enabled=False,grant=None)
            self._save(item); self.cancel(item['id'])
            self.controller.emit('skill.drafted',{'id':item['id'],'digest':content_digest,'author':author},phase='completed')
            result.append(item)
        return {'skills':result}

    def activate(self, identifier, digest):
        item=self._get(identifier)
        if item['digest']!=digest: raise ValueError('Skill changed; review the current version')
        item.update(approved_digest=digest,enabled=True)
        self._save(item); self._register_extension(item)
        self.controller.emit('skill.activated',{'id':identifier,'digest':digest},phase='completed')
        return item

    def disable(self, identifier):
        item=self._get(identifier); item.update(enabled=False,grant=None); self._save(item); self.cancel(identifier)
        self.controller.emit('skill.disabled',{'id':identifier},phase='completed'); return item

    def grant(self, identifier, digest, capabilities, entity_scope, budgets=None):
        item=self._active(identifier,digest)
        if not capabilities or not all(any(fnmatchcase(c,p) for p in item['capabilities']) for c in capabilities): raise ValueError('Grant exceeds declared capabilities')
        if not entity_scope or not all(any(s==p or (not any(c in s for c in '*?[') and fnmatchcase(s,p)) for p in item['scope']) for s in entity_scope): raise ValueError('Grant scope exceeds declared scope')
        limits=execution_budgets(item.get('budgets',DEFAULT_BUDGETS) | (budgets or {}))
        item['grant']={'digest':digest,'capabilities':capabilities,'scope':entity_scope,'budgets':limits}
        self._save(item); self.controller.emit('skill.granted',{'id':identifier,'grant':item['grant']},phase='completed')
        return item

    def revoke(self, identifier):
        item=self._get(identifier); item['grant']=None; self._save(item); self.cancel(identifier)
        self.controller.emit('skill.revoked',{'id':identifier},phase='completed'); return item

    def pause(self, paused=True):
        if type(paused) is not bool: raise ValueError('paused must be boolean')
        self.controller.emit('controller.autonomy',{'paused':paused},phase='committed',
                             _records=[{'table':'settings','id':'autonomy_paused','state':paused}])
        if paused:
            for identifier in list(self.running): self.cancel(identifier)
        return {'paused':paused}

    def _active(self, identifier, digest=None):
        item=self._get(identifier)
        if not item['enabled'] or item['approved_digest']!=item['digest'] or (digest and digest!=item['digest']):
            raise ValueError('Skill version is not approved and enabled')
        return item

    def _restore_extensions(self):
        for item in self._items():
            if item['enabled'] and item['approved_digest']==item['digest']: self._register_extension(item)

    def _register_extension(self,item):
        name='plugin.'+item['id']+'.run'
        self.controller._commands.pop(name,None); self.controller._handlers.pop(name,None)
        self.controller.register(name,lambda p,i=item['id']: self.run(i,p),input_schema=item['input_schema'],mutating=True,external=True)
        with self.journal.connect() as db:
            for event_name,event_schema in item.get('event_schemas',{}).items():
                db.execute('INSERT OR REPLACE INTO event_types VALUES (?,?)',(event_name,models.dumps({'name':event_name,'payload_schema':event_schema,'plugin':item['plugin'],'version':item['plugin_version']})))

    def start(self):
        if self.worker is not None: return
        # Reuse P&R's kernel-owned lock. Readers and CLI discovery cannot steal deliveries.
        service_lock=FileLock(self.controller.directory/'subscriptions',timeout=0)
        service_lock.__enter__(); self.service_lock=service_lock
        with self.journal.connect() as db: db.execute("UPDATE deliveries SET status='indeterminate' WHERE status='running'")
        self.worker=threading.Thread(target=self._work,daemon=True,name='controller-skills'); self.worker.start()

    def notify(self, event):
        if self.stop.is_set(): return
        # Never start a service merely because state was read by a short-lived CLI.
        if self.worker is None: return
        for item in self._items():
            if not item['enabled'] or item['digest']!=item['approved_digest']: continue
            if event.get('skill')==item['id']: continue
            if event.get('depth',0)>=((item.get('grant') or {}).get('budgets') or item.get('budgets',DEFAULT_BUDGETS))['max_depth']: continue
            if not any(fnmatchcase(event['type'],pattern) for pattern in item['triggers']): continue
            with self.journal.connect() as db:
                cursor=db.execute('INSERT OR IGNORE INTO deliveries VALUES (?,?,?)',(item['id'],event['id'],'pending'))
            if cursor.rowcount: self.queue.put((item['id'],event))

    def _work(self):
        # Recover never-started deliveries without repeating interrupted scripts.
        with self.journal.connect() as db:
            pending=list(db.execute("SELECT skill,event_id FROM deliveries WHERE status='pending'"))
        events={e['id']:e for e in self.journal.events()}
        for row in pending:
            if row['event_id'] in events: self.queue.put((row['skill'],events[row['event_id']]))
        while not self.stop.is_set():
            try: identifier,event=self.queue.get(timeout=.2)
            except queue.Empty: continue
            with self.journal.connect() as db:
                cursor=db.execute("UPDATE deliveries SET status='running' WHERE skill=? AND event_id=? AND status='pending'",(identifier,event['id']))
            if not cursor.rowcount: continue
            try:
                result=self.run(identifier,{'event':event},event=event)
                status='completed' if result['status']=='success' else 'failed'
            except Exception as exc:
                status='failed'; self.controller.emit('skill.delivery_failed',{'id':identifier,'error':str(exc)},phase='failed')
            with self.journal.connect() as db: db.execute('UPDATE deliveries SET status=? WHERE skill=? AND event_id=?',(status,identifier,event['id']))

    def _start_broker(self):
        if self.broker is not None: return
        service=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def do_POST(self):
                try:
                    token=self.headers.get('Authorization','').removeprefix('Bearer ')
                    with service.lock: run=service.tokens.get(token)
                    if not run: raise ValueError('Expired controller capability token')
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=2000000: raise ValueError('Invalid broker request size')
                    payload=json.loads(self.rfile.read(length))
                    with scope(service.controller,run['correlation'],run['cause'],run['depth'],run['id']):
                        result=service._broker_call(run,self.path,payload)
                    body=json.dumps(result,ensure_ascii=False).encode(); status=200
                except Exception as exc: body=json.dumps({'error':str(exc)}).encode(); status=400
                self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        self.broker=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.broker_thread=threading.Thread(target=self.broker.serve_forever,daemon=True); self.broker_thread.start()

    @staticmethod
    def _scope_allowed(targets,patterns):
        def allowed(target):
            related=[target]
            if target.startswith('turn:'): related.append('chat:'+target[5:].rsplit(':',1)[0])
            if target.startswith('index_part:'): related.append('context:'+target[11:])
            return any(fnmatchcase(t,p) for t in related for p in patterns)
        return bool(patterns) and all(allowed(t) for t in targets)

    def _broker_call(self,run,path,data):
        item=self._active(run['id'],run['digest'])
        if path=='/inspect':
            selection=data.get('selection')
            from .visuals import all_entities
            offset=data.get('offset',0); limit=data.get('limit',100)
            if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=1000: raise ValueError('Invalid inspection page')
            seq=self.controller._at(data.get('at_sequence'))
            entities=[e for e in all_entities(self.controller,selection,seq) if self._scope_allowed([f"{e['kind']}:{e['id']}"],item['scope'])]
            inspected={'schema':'controller-inspection/1','at_sequence':seq,'publication_lag':self.controller.publication_lag(),
                       'entities':entities[offset:offset+limit],'total':len(entities),'next_offset':offset+limit if offset+limit<len(entities) else None}
            self.controller.emit('controller.inspect',{'selection':selection,'at_sequence':inspected['at_sequence']},phase='completed')
            return inspected
        if path=='/result':
            fastjsonschema.validate(item['output_schema'],data['result']); run['result']=data['result']; return {'status':'recorded'}
        if path=='/emit':
            if not data['event'].startswith('plugin.'+item['plugin']+'.'): raise ValueError('Plugin events must use their namespace')
            fastjsonschema.validate(item.get('event_schemas',{}).get(data['event'],{}),data['payload'])
            return self.controller.emit(data['event'],data['payload'])
        if path=='/view':
            return self._request_action(run,item,'controller.view.publish',{'name':item['id']+'.'+data['name'],'definition':data['definition']},None,None)
        if path=='/dispatch':
            return self._request_action(run,item,data['command'],data['payload'],data.get('request_id'),data.get('expected_revision'))
        raise ValueError('Unknown SDK operation')

    def _request_action(self,run,item,command,payload,request_id,revision):
        if command not in self.controller._commands: raise ValueError('Unknown controller command')
        definition=self.controller._commands[command]; capability=definition['capability']
        if not any(fnmatchcase(capability,p) for p in item['capabilities']): raise ValueError('Action exceeds skill capabilities')
        targets=self.controller.targets(command,payload)
        if not self._scope_allowed(targets,item['scope']): raise ValueError('Action exceeds skill entity scope')
        payload=dict(payload)
        if revision is not None: payload['revision']=revision
        if definition['revision_required'] and 'revision' not in payload:
            if command.startswith('pandr.'): payload['revision']=self.controller.pandr.revision
            else:
                rows=[e for e in self.controller.inspect(targets)['entities'] if f"{e['kind']}:{e['id']}" in targets]
                if len(rows)!=1: raise ValueError('A target revision is required')
                payload['revision']=rows[0]['state']['revision']
        self.controller._handlers[command][1](payload)
        with self.lock:
            run['actions']+=1
            if run['actions']>run['budgets']['max_actions']: raise ValueError('Skill action budget exceeded')
        item=self._active(run['id'],run['digest'])
        grant=item.get('grant')
        allowed=grant and grant['digest']==item['digest'] and not self.journal.setting('autonomy_paused',False)
        allowed=allowed and any(fnmatchcase(capability,p) for p in grant['capabilities']) and self._scope_allowed(targets,grant['scope'])
        # Activation, grants and approval always require a human review, even if a plugin requests them.
        if command in ('skill.activate','skill.grant','proposal.approve','skill.pause'): allowed=False
        request_id=request_id or str(uuid.uuid4())
        if allowed: return {'status':'executed','result':self.controller.dispatch(command,payload,request_id)}
        from .visuals import all_entities
        before=all_entities(self.controller,targets,self.controller.journal.head())
        proposal={'id':str(uuid.uuid4()),'skill':item['id'],'skill_digest':item['digest'],'command':command,'payload':payload,
                  'request_id':request_id,'targets':targets,'before':before,
                  'expected_change':payload,'status':'pending','correlation':run['correlation'],'cause':run['cause'],'depth':run['depth']}
        proposal['digest']=models.sha(models.dumps(proposal).encode())
        self._record_proposal('skill.proposed',proposal,'proposed')
        return {'status':'approval_required','proposal':proposal}

    def approve(self,identifier,digest):
        with self._proposal_lock(identifier):
            proposal=self._proposal(identifier)
            if proposal['digest']!=digest or proposal['status']!='pending': raise ValueError('Proposal changed or is no longer pending')
            self._active(proposal['skill'],proposal['skill_digest'])
            proposal['status']='executing'; self._record_proposal('skill.approval_started',proposal,'started')
            try:
                with scope(self.controller,proposal['correlation'],proposal['cause'],proposal['depth'],proposal['skill']):
                    result=self.controller.dispatch(proposal['command'],proposal['payload'],proposal['request_id'])
                proposal.update(status='completed',result=result)
            except Exception as exc:
                with self.journal.connect() as db:
                    request=db.execute('SELECT status FROM requests WHERE id=?',(proposal['request_id'],)).fetchone()
                proposal.update(status='indeterminate' if request and request[0]=='indeterminate' else 'failed',error=str(exc))
            self._record_proposal('skill.approval',proposal,proposal['status'])
        return proposal

    def reject(self,identifier):
        with self._proposal_lock(identifier):
            proposal=self._proposal(identifier)
            if proposal['status']!='pending': raise ValueError('Proposal is no longer pending')
            proposal['status']='rejected'; self._record_proposal('skill.rejected',proposal,'completed')
        return proposal

    def run(self,identifier,input_data=None,event=None):
        item=self._active(identifier)
        input_data=input_data or {}; fastjsonschema.validate(item['input_schema'],input_data)
        budgets=(item.get('grant') or {}).get('budgets',item.get('budgets',DEFAULT_BUDGETS))
        parent=event or CURRENT.get() or {}
        depth=parent.get('depth',0)+1
        if depth>budgets['max_depth']: raise ValueError('Skill causal depth exceeded')
        run_id=str(uuid.uuid4()); token=secrets.token_urlsafe(32)
        run={'id':identifier,'run_id':run_id,'digest':item['digest'],'actions':0,'budgets':budgets,'result':None,
             'correlation':parent.get('correlation') or run_id,'cause':parent.get('id') or parent.get('cause'),'depth':depth}
        runner=ExecutionService()
        with self.lock:
            if identifier in self.running: raise ValueError('This skill is already running')
            self._start_broker(); self.tokens[token]=run; self.running[identifier]=runner
        directory=self.controller.directory/'runs'/run_id; directory.mkdir(parents=True)
        payload_path=directory/'input.json'; payload_path.write_text(json.dumps(input_data,ensure_ascii=False),encoding='utf-8')
        url=f'http://127.0.0.1:{self.broker.server_port}'
        bootstrap=(f"import os\nos.environ['CONTROLLER_BROKER']={url!r}\nos.environ['CONTROLLER_TOKEN']={token!r}\nos.environ['CONTROLLER_INPUT']={str(payload_path)!r}\n"
                   "from framelm.controller.sdk import Client\ncontroller=Client()\ninput_data=controller.input\n"+
                   item['source']+"\ncontroller.complete(globals().get('result'))\n")
        with scope(self.controller,run['correlation'],run['cause'],depth,identifier):
            self.controller.emit('skill.started',{'id':identifier,'run_id':run_id,'digest':item['digest']},phase='started')
            try:
                before=self.controller.pandr.snapshot()
                result=runner.run_script(bootstrap,self.controller.pandr.path,cwd=directory,timeout=budgets['timeout'],max_output=budgets['max_output'])
                self.controller.pandr.reload()
                after=self.controller.pandr.snapshot()
                if before!=after:
                    self.controller.emit('pandr.script_outcome',{'run_id':run_id,'status':result['status']},phase='committed',changes=[{'kind':'pandr','id':'main','state':after}])
                result.update(skill=identifier,run_id=run_id,skill_digest=item['digest'],result=run['result'],actions=run['actions'])
                self.controller.emit('skill.finished',result,phase='completed' if result['status']=='success' else 'failed',
                                     changes=[{'kind':'skill_run','id':run_id,'state':result}])
                return result
            finally:
                with self.lock: self.tokens.pop(token,None); self.running.pop(identifier,None)

    def cancel(self,identifier):
        with self.lock:
            runner=self.running.get(identifier)
            if runner: runner.cancel_all()
        result={'id':identifier,'cancellation_requested':bool(runner)}
        if runner: self.controller.emit('skill.cancel',result,phase='requested')
        return result

    def close(self):
        self.stop.set()
        for identifier in list(self.running): self.cancel(identifier)
        if self.worker: self.worker.join(timeout=5)
        if self.broker:
            self.broker.shutdown(); self.broker.server_close(); self.broker_thread.join(timeout=5)
        if self.service_lock: self.service_lock.__exit__(None,None,None); self.service_lock=None
