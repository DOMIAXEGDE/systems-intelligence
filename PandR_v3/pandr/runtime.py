"""Headless P&R command planning, contracts, artifacts and durable communications."""
from __future__ import annotations
import copy
import hashlib
import os
from pathlib import Path
import tempfile
import uuid
import zipfile
from .canonical import canonical, digest, content_digest, strict_json
from .errors import ValidationError, BudgetExceeded, RevisionConflict, ContractError
from .repository import Repository, now, seal, validate_document, validate_domain
from . import contracts

DEFAULT_BUDGETS={'max_points':4096,'max_events':10000,'max_terms':1024,'max_operations':10000,
                 'max_degree':32,'max_queue':1024,'max_output_bytes':16777216,'max_ticks':10000}

def _default_points():
    return [{'id':f'p{i}','x':x,'y':y} for i,(x,y) in enumerate([
        (2,17),(3,7),(5,3),(7,23),(37,11),(-11,13),(-19,31),(-29,5),
        (-5,-19),(-13,-29),(-31,-11),(11,-13),(23,-31),(41,-5)])]

def _initial_state(points=None,session_id=None,domain=None):
    from .expressions import Function
    from .geometry import validate_points
    from .alphabets import create_alphabet
    if domain is None:
        points=validate_points(points if points is not None else _default_points())
        fabric={'id':str(uuid.uuid4()),'points':points}
        fabric['digest']=content_digest(fabric)
        alphabet=create_alphabet()
        domain={'fabric':fabric,'functions':{'main':Function.parse('x').to_dict()},
                'geometry':{'profile':'contacts-xy-v2','window':['-257','257'],'root_policy':'unique'},
                'layers':[{'id':kind,'type':kind,'enabled':True,'config':{},'state':{}}
                          for kind in ['geometry','sequence','numeric','alphabet','text']],
                'alphabets':{alphabet['id']:alphabet},'selected_alphabet':alphabet['id'],
                'contracts':contracts.defaults(),'sequences':[], 'routes':[],
                'budgets':copy.deepcopy(DEFAULT_BUDGETS),'tick':0,'last_events':[],
                'last_ranks':[],'received':[]}
    timestamp=now()
    state={'schema':'pandr-session-v2','schema_version':2,'session_id':session_id or str(uuid.uuid4()),
           'revision':0,'engine':{'version':'2.0.0','geometry':'contacts-xy-v2','codec':'domalec-shortlex-repo-v1'},
           'domain':copy.deepcopy(domain),'programs':{},'runs':{},'receipts':[], 'request_ledger':{},
           'replay_checkpoint':{'domain':copy.deepcopy(domain),'domain_hash':digest(domain)},
           'transition_log':[],'inbox':[],'outbox':[],'delivery_ledger':{},'artifacts':{},
           'memory':{'workspace_notes':''},'ui':{},'integrity':{},
           'created_at':timestamp,'updated_at':timestamp}
    return seal(state)

class TransformPlan:
    def __init__(self,plan): self.plan=plan
    def translate(self,dx='0',dy='0'): return self.plan._add('translate',dx=str(dx),dy=str(dy))
    def scale(self,sx='1',sy='1'): return self.plan._add('scale',sx=str(sx),sy=str(sy))
    def reflect_x(self): return self.plan._add('reflect_x')
    def reflect_y(self): return self.plan._add('reflect_y')
    def precompose(self,function): return self.plan._add('precompose',function=_function(function))
    def postcompose(self,function): return self.plan._add('postcompose',function=_function(function))

def _function(value):
    from .expressions import Function
    return (Function.parse(value) if isinstance(value,str) else value).to_dict()

class Plan:
    def __init__(self,runtime,expected_revision=None,request_id=None):
        self.snapshot=runtime.snapshot()
        self.expected_revision=self.snapshot['revision'] if expected_revision is None else expected_revision
        self.expected_payload=self.snapshot['integrity']['payload_hash']
        self.request_id=request_id or str(uuid.uuid4())
        self.commands=[]; self.transform=TransformPlan(self)
    def _add(self,op,**fields):
        record={'op':op,**fields}; canonical(record); self.commands.append(record); return self
    def set_function(self,function): return self._add('set_function',function=_function(function))
    def resolve(self,sequence,**options): return self._add('resolve',sequence=sequence,options=options)
    def define_alphabet(self,manifest): return self._add('define_alphabet',manifest=copy.deepcopy(manifest))
    def render(self,kind,payload=None,parameters=None):
        return self._add('render',kind=kind,payload=payload,parameters=parameters or {})
    def add_layer(self,kind,identifier=None,**config):
        return self._add('add_layer',type=kind,id=identifier or kind,config=config)
    def configure_layer(self,identifier,changes):
        return self._add('configure_layer',id=identifier,changes=copy.deepcopy(changes))
    def remove_layer(self,identifier): return self._add('remove_layer',id=identifier)
    def save_script(self,identifier,source): return self._add('save_script',id=identifier,source=source)
    def set_notes(self,text): return self._add('set_notes',text=text)
    def set_geometry(self,**changes): return self._add('set_geometry',changes=changes)
    def set_budgets(self,**changes): return self._add('set_budgets',changes=changes)
    def register_contract(self,contract): return self._add('register_contract',contract=contract)
    def connect(self,source_layer,destination,**options):
        return self._add('connect',source_layer=source_layer,destination=str(Path(destination).resolve()),options=options)
    def send(self,destination,payload=None,payload_type='rank-stream',**options):
        return self._add('send',destination=str(Path(destination).resolve()),payload=payload,
                         payload_type=payload_type,options=options)
    def set_fabric(self, points): return self._add('set_fabric', points=copy.deepcopy(points))

class Runtime:
    def __init__(self,repository):
        self.repository=repository; self.path=repository.path; self._execution_service=None
    @classmethod
    def create(cls,path,points=None):
        repository=Repository(path); repository.create(_initial_state(points)); return cls(repository)
    @classmethod
    def open(cls,path):
        repository=Repository(path); repository.load(); return cls(repository)
    def __enter__(self): return self
    def __exit__(self,*args): return False
    @property
    def revision(self): return self.repository.snapshot()['revision']
    def snapshot(self): return self.repository.snapshot()

    def plot(self, functions=None, *, x_range=(-10, 10), y_range=(-10, 10),
             resolution=400, output=None, **options):
        """Numerically plot the committed function, a function plan, or custom curves.

        A plan is previewed without committing it. This returns a Plot; pass an
        output path or call Plot.save() to export PNG/SVG. It never emits contacts.
        """
        from .expressions import Function
        from .plotting import plot
        if isinstance(functions, Plan):
            function = Function.from_dict(functions.snapshot['domain']['functions']['main'])
            for command in functions.commands:
                op = command['op']
                if op == 'set_function': function = Function.from_dict(command['function'])
                elif op in ('translate', 'scale'):
                    function = getattr(function, op)(**{k: v for k, v in command.items() if k != 'op'})
                elif op in ('reflect_x', 'reflect_y'): function = getattr(function, op)()
                elif op in ('precompose', 'postcompose'):
                    function = getattr(function, op)(Function.from_dict(command['function']))
            functions = function
        if functions is None:
            functions = Function.from_dict(self.snapshot()['domain']['functions']['main'])
        result = plot(functions, x_range=x_range, y_range=y_range, resolution=resolution, **options)
        if output is not None: result.save(output)
        return result

    def plot_explicit(self, function=None, **options):
        """Plot y=f(x); defaults to the committed function."""
        return self.plot(function, **options)

    def plot_implicit(self, function=None, **options):
        """Plot F(x,y)=0, or preview a plan using its stored curve type."""
        from .plotting import Curve
        if function is not None and not isinstance(function, Plan):
            function = Curve.implicit(function)
        return self.plot(function, **options)
    def reload(self): return self.repository.load()
    def plan(self,expected_revision=None,request_id=None): return Plan(self,expected_revision,request_id)
    def validate(self):
        state=validate_document(self.snapshot())
        for artifact in state['artifacts'].values():
            path=self._artifact_path(artifact['path'])
            if not path.is_file() or path.stat().st_size!=artifact['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest()!=artifact['sha256']:
                raise ValidationError(f"Missing/corrupt artifact: {artifact['path']}")
        return {'status':'valid','revision':state['revision'],'domain_hash':state['integrity']['domain_hash']}
    def _artifact_path(self,relative):
        path=(self.path.parent/relative).resolve()
        if not path.is_relative_to(self.path.parent): raise ValidationError('Artifact traversal')
        return path
    def measure(self,plane='horizontal',channel='v',policy=None,**options):
        from .geometry import measurements
        from .expressions import Function
        domain=self.snapshot()['domain']
        return measurements(Function.from_dict(domain['functions']['main']),domain['fabric']['points'],
            plane=plane,channel=channel,policy=policy or domain['geometry']['root_policy'],
            window=domain['geometry']['window'],budget=domain['budgets'],**options)
    def resolve(self,sequence,**options):
        from .sequences import resolve_sequence, parse_sequence
        from .expressions import Function
        domain=self.snapshot()['domain']
        options=dict(options)
        if 'root_policy' in options: options['policy']=options.pop('root_policy')
        plane=options.pop('plane',None)
        if plane:
            sequence=_selectors_with_plane(sequence,plane)
        return resolve_sequence(sequence,Function.from_dict(domain['functions']['main']),domain['fabric']['points'],
            window=domain['geometry']['window'],policy=options.pop('policy',domain['geometry']['root_policy']),
            budget=domain['budgets'],**options)
    def save_ui(self,value):
        canonical(value)
        return self.repository.metadata(lambda state:state['ui'].update(copy.deepcopy(value)))
    def record_execution(self,result,script_id):
        """Keep the exact captured result attached to its immutable source digest."""
        run_id=result.get('run_id','python:'+str(uuid.uuid4()))
        value=copy.deepcopy(result); value['script_id']=script_id; value['run_id']=run_id
        def save(state):
            source=state['programs'].get(script_id)
            value['target_changed']=source is None or hashlib.sha256(source['source'].encode('utf-8')).hexdigest()!=value.get('source_digest')
            state['runs'][run_id]=value
        self.repository.metadata(save)
        return value
    def execute_script(self,identifier,timeout=30,max_output=262144,on_output=None):
        from .execution import ExecutionService
        self.reload(); state=self.snapshot(); program=state['programs'].get(identifier)
        if program is None: raise ValidationError('Unknown operator script')
        if self._execution_service is None: self._execution_service=ExecutionService()
        run_id='python:'+str(uuid.uuid4())
        running={'run_id':run_id,'status':'running','script_id':identifier,'source':program['source'],
                 'source_digest':hashlib.sha256(program['source'].encode('utf-8')).hexdigest(),
                 'input_revision':state['revision'],'input_domain_hash':state['integrity']['domain_hash'],
                 'owner_pid':os.getpid(),'trusted_python':True}
        self.repository.metadata(lambda s:s['runs'].update({run_id:running}))
        try:
            result=self._execution_service.run_script(program['source'],self.path,cwd=self.path.parent,
                                                      timeout=timeout,max_output=max_output,on_output=on_output)
        except Exception as error:
            result={'status':'error','output':'','returncode':None,'source_digest':running['source_digest'],
                    'error':f'{type(error).__name__}: {error}','trusted_python':True,
                    'construction_replayable':False,'duration_ms':0}
        result.update({key:running[key] for key in ('run_id','source','input_revision','input_domain_hash','owner_pid')})
        return self.record_execution(result,identifier)
    def cancel_scripts(self):
        if self._execution_service is not None: self._execution_service.cancel_all()
    def _check_layer(self,domain,kind):
        if not any(layer['type']==kind and layer['enabled'] for layer in domain['layers']):
            raise ContractError(f'An enabled {kind} layer is required')

    def _evaluate(self,plan,staging,contract):
        from .expressions import Function
        from .sequences import resolve_sequence, parse_sequence
        from .alphabets import validate_alphabet
        from .artifacts import render_artifact
        from .codecs import ShortlexCodec, GlyphRankCodec
        before=plan.snapshot['domain']; domain=copy.deepcopy(before)
        result={'domain':domain,'outputs':[],'artifacts':[],'outbox':[],
                'programs':copy.deepcopy(plan.snapshot['programs']),
                'memory':copy.deepcopy(plan.snapshot['memory']),'received':[]}
        contracts.check(contract,'preconditions',before,domain,plan.commands,[])
        outputs=result['outputs']; budgets=domain['budgets']
        def project(events):
            ranks=[]
            for event in events:
                number=event['magnitude']
                if number['kind']!='rational' or number['d']!='1' or number['n'].startswith('-'):
                    raise ValidationError('Exact nonnegative integer ranks required; select a different measurement')
                ranks.append(number['n'])
            return ranks
        def render(kind,payload,parameters):
            self._check_layer(domain,kind)
            alphabet=domain['alphabets'][domain['selected_alphabet']]
            if payload is None:
                if kind in ('text','code'):
                    decoded=''.join(ShortlexCodec().decode(rank) for rank in domain['last_ranks'])
                    payload=decoded if kind=='text' else 'print('+repr(decoded)+')\n'
                elif kind=='image':
                    codec=GlyphRankCodec(alphabet['symbols'])
                    payload=[glyph for rank in domain['last_ranks'] for glyph in codec.decode(rank)]
                else: payload=[int(rank) for rank in domain['last_ranks']]
            parameters=copy.deepcopy(parameters)
            parameters['max_output_bytes']=min(parameters.get('max_output_bytes',budgets['max_output_bytes']),budgets['max_output_bytes'])
            output_dir=staging/str(len(result['artifacts'])); output_dir.mkdir(parents=True,exist_ok=True)
            artifact=render_artifact(kind,payload,parameters,output_dir,alphabet=alphabet)
            filename=Path(artifact['path']).name
            source=output_dir/filename
            if not source.exists(): raise ValidationError('Renderer did not produce its artifact')
            extension=source.suffix
            artifact['path']='artifacts/'+artifact['sha256']+extension
            artifact['provenance']={'domain_hash':digest(domain),'request_id':plan.request_id,
                'alphabet_digest':alphabet['digest'],'codec':'domalec-shortlex-repo-v1' if kind in ('text','code') else 'pandr-glyph-shortlex-v1'}
            # Persist auxiliary frame/timeline files as hashed dependencies too.
            dependencies=[]
            for extra in output_dir.rglob('*'):
                if extra.is_file() and extra!=source:
                    data=extra.read_bytes(); sha=hashlib.sha256(data).hexdigest()
                    dependencies.append({'path':'artifacts/'+extra.name,'sha256':sha,'bytes':len(data),
                                         'source':str(extra)})
            for reference in ('glyph_stream','timeline'):
                if reference in artifact:
                    artifact[reference]='artifacts/'+Path(artifact[reference]).name
            result['artifacts'].append({'manifest':artifact,'source':str(source),'dependencies':dependencies})
            outputs.append({'type':'artifact','data':artifact})
            return artifact
        def send(destination,payload,payload_type,options,source_layer='sequence'):
            if len(plan.snapshot['outbox'])+len(result['outbox'])>=budgets['max_queue']:
                raise BudgetExceeded('Outbox retention/queue budget exhausted; export/checkpoint before continuing')
            if payload is None: payload=domain['last_ranks']
            canonical(payload)
            if payload_type=='rank-stream': _validate_ranks(payload)
            elif payload_type not in ('artifact-reference','text','events'):
                raise ValidationError('Unsupported port payload type')
            payload_hash=digest(payload)
            stream=digest([plan.snapshot['session_id'],destination,source_layer])
            count=sum(e['stream_id']==stream for e in plan.snapshot['outbox']+result['outbox'])
            hop=options.get('hop_count',0); max_hops=options.get('max_hops',16)
            if type(hop) is not int or type(max_hops) is not int or not 0<=hop<max_hops<=256:
                raise BudgetExceeded('Signal hop budget exhausted')
            envelope={'protocol':'pandr-signal-v2','message_id':digest([plan.snapshot['session_id'],plan.request_id,len(result['outbox']),payload_hash]),
                'transaction_id':plan.request_id,'source_session':plan.snapshot['session_id'],
                'source_layer':source_layer,'source_port':'output','source_revision':plan.snapshot['revision'],
                'source_domain_hash':digest(before),'destination':destination,'destination_layer':options.get('destination_layer','numeric'),
                'destination_port':'input','expected_revision':options.get('expected_revision'),
                'expected_domain_hash':options.get('expected_domain_hash'), 'stream_id':stream,'sequence':count,
                'logical_tick':domain['tick'],'hop_count':hop,'max_hops':max_hops,'payload_type':payload_type,
                'payload':copy.deepcopy(payload),'payload_digest':payload_hash,'codec':'domalec-shortlex-repo-v1',
                'correlation_id':options.get('correlation_id',plan.request_id),'status':'pending'}
            result['outbox'].append(envelope); outputs.append({'type':'signal','data':envelope})
        def route(source_layer,options=None):
            for link in domain['routes']:
                if link['source_layer']==source_layer:
                    merged={**link.get('options',{}),**(options or {})}
                    send(link['destination'],None,'rank-stream',merged,source_layer)
        for command in plan.commands:
            op=command['op']
            if op in {'set_function','translate','scale','reflect_x','reflect_y','precompose','postcompose'}:
                self._check_layer(domain,'geometry')
                function=Function.from_dict(domain['functions']['main'])
                if op=='set_function': function=Function.from_dict(command['function'])
                elif op=='translate': function=function.translate(command['dx'],command['dy'])
                elif op=='scale': function=function.scale(command['sx'],command['sy'])
                elif op in ('reflect_x','reflect_y'): function=getattr(function,op)()
                else: function=getattr(function,op)(Function.from_dict(command['function']))
                domain['functions']['main']=function.to_dict()
            elif op=='resolve':
                self._check_layer(domain,'sequence'); options=copy.deepcopy(command['options'])
                policy=options.pop('root_policy',options.pop('policy',domain['geometry']['root_policy']))
                selectors=parse_sequence(command['sequence']) if isinstance(command['sequence'],str) else copy.deepcopy(command['sequence'])
                plane=options.pop('plane',None)
                if plane:
                    selectors=_selectors_with_plane(command['sequence'],plane)
                if len(selectors)>budgets['max_terms']: raise BudgetExceeded('Sequence term budget exceeded')
                events=resolve_sequence(selectors,Function.from_dict(domain['functions']['main']),domain['fabric']['points'],
                    window=domain['geometry']['window'],policy=policy,budget=budgets,**options)
                if len(events)>budgets['max_events']: raise BudgetExceeded('Event budget exceeded')
                domain['last_events']=events; domain['last_ranks']=project(events)
                domain['sequences'].append({'source':command['sequence'],'selectors':selectors,'options':command['options']})
                outputs.append({'type':'events','data':events}); route('sequence')
            elif op=='define_alphabet':
                self._check_layer(domain,'alphabet'); manifest=copy.deepcopy(command['manifest']); validate_alphabet(manifest)
                old=domain['alphabets'].get(manifest['id'])
                if old and old['digest']!=manifest['digest'] and manifest['version']<=old['version']:
                    raise ValidationError('Alphabet edits require a higher version')
                domain['alphabets'][manifest['id']]=manifest; domain['selected_alphabet']=manifest['id']
                outputs.append({'type':'alphabet','data':manifest})
            elif op=='render': render(command['kind'],command['payload'],command['parameters'])
            elif op=='add_layer':
                if any(layer['id']==command['id'] for layer in domain['layers']): raise ValidationError('Layer ID exists')
                domain['layers'].append({'id':command['id'],'type':command['type'],'enabled':True,'config':command['config'],'state':{}})
            elif op=='configure_layer':
                changes=command['changes']
                if not set(changes)<={'enabled','config'}: raise ValidationError('Only enabled/config can be configured')
                layer=next((item for item in domain['layers'] if item['id']==command['id']),None)
                if layer is None: raise ValidationError('Layer not found')
                layer.update(copy.deepcopy(changes))
            elif op=='remove_layer':
                if command['id'] not in {layer['id'] for layer in domain['layers']}: raise ValidationError('Layer not found')
                domain['layers']=[layer for layer in domain['layers'] if layer['id']!=command['id']]
            elif op=='save_script':
                if not isinstance(command['source'],str) or len(command['source'])>1000000: raise ValidationError('Script text budget')
                result['programs'][command['id']]={'id':command['id'],'source':command['source'],'digest':digest(command['source'])}
            elif op=='set_notes':
                if not isinstance(command['text'],str) or len(command['text'])>1000000: raise ValidationError('Notes text budget')
                result['memory']['workspace_notes']=command['text']
            elif op=='set_geometry':
                if not set(command['changes'])<={'window','root_policy'}: raise ValidationError('Immutable geometry profile')
                domain['geometry'].update(command['changes'])
            elif op=='set_fabric':
                from .geometry import validate_points
                import uuid
                points = validate_points(command['points'])
                fabric={'id':str(uuid.uuid4()),'points':points}
                fabric['digest']=content_digest(fabric)
                domain['fabric'] = fabric
            elif op=='set_budgets':
                if not set(command['changes'])<=set(DEFAULT_BUDGETS): raise ValidationError('Unknown budget')
                domain['budgets'].update(command['changes'])
            elif op=='register_contract':
                record=contracts.validate_contract(command['contract']); old=domain['contracts'].get(record['id'])
                if old and old['digest']!=record['digest'] and record['version']<=old['version']:
                    raise ValidationError('Contract change requires a new version')
                domain['contracts'][record['id']]=record
            elif op=='connect':
                domain['routes'].append({k:v for k,v in command.items() if k!='op'})
            elif op=='send': send(command['destination'],command['payload'],command['payload_type'],command['options'])
            elif op=='receive':
                envelope=command['envelope']; _validate_envelope(envelope)
                expected=envelope.get('expected_revision'); expected_hash=envelope.get('expected_domain_hash')
                if expected is not None and expected!=plan.snapshot['revision']: raise RevisionConflict('Destination revision mismatch')
                if expected_hash is not None and expected_hash!=digest(before): raise RevisionConflict('Destination domain hash mismatch')
                layer=next((item for item in domain['layers'] if item['id']==envelope['destination_layer'] and item['enabled']),None)
                if layer is None: raise ContractError('Destination layer is missing or disabled')
                known=[item for item in domain['received'] if item['stream_id']==envelope['stream_id']]
                if envelope['sequence']!=len(known): raise ValidationError('Stream gap/out-of-order delivery; retry predecessor first')
                if len(domain['received'])>=budgets['max_queue']: raise BudgetExceeded('Consumer retained-message budget exhausted')
                domain['received'].append({'message_id':envelope['message_id'],'stream_id':envelope['stream_id'],
                    'payload_digest':envelope['payload_digest'],'sequence':envelope['sequence']})
                result['received'].append(envelope)
                if envelope['payload_type']=='rank-stream':
                    domain['last_ranks']=copy.deepcopy(envelope['payload'])
                    for kind in layer['config'].get('on_rank_stream',[]): render(kind,None,{})
                    route(layer['id'],{'hop_count':envelope['hop_count']+1,'max_hops':envelope['max_hops'],
                                      'correlation_id':envelope['correlation_id']})
                outputs.append({'type':'received','data':{'message_id':envelope['message_id']}})
            else: raise ValidationError(f'Unknown command {op}')
            validate_domain(domain)
        domain['tick']+=1
        if domain['tick']>budgets['max_ticks']: raise BudgetExceeded('Logical tick budget exhausted')
        contracts.check(contract,'postconditions',before,domain,plan.commands,outputs)
        return result

    def dry_run(self,plan,contract='operator-v2'):
        record=plan.snapshot['domain']['contracts'].get(contract)
        if record is None: raise ContractError('Unknown contract')
        with tempfile.TemporaryDirectory(prefix='pandr-dry-') as temp:
            result=self._evaluate(plan,Path(temp),record)
            return {'status':'preview','domain_hash':digest(result['domain']),'outputs':result['outputs']}
    def commit(self,plan,contract='operator-v2'):
        if not isinstance(plan,Plan) or plan.snapshot['session_id']!=self.snapshot()['session_id']:
            raise ValidationError('Plan belongs to a different session')
        request_digest=digest({'commands':plan.commands,'contract':contract})
        # Avoid reevaluating a retry whose input state may no longer be usable.
        latest=Repository(self.path); current=latest.load(recover=False)
        if plan.request_id in current['request_ledger']:
            return self.repository.commit(plan.expected_revision,plan.expected_payload,plan.request_id,request_digest,lambda _:None)
        record=plan.snapshot['domain']['contracts'].get(contract)
        if record is None: raise ContractError(f'Unknown contract: {contract}')
        staging_root=self.path.parent/'.staging'; staging_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='run-',dir=staging_root) as temp:
            result=self._evaluate(plan,Path(temp),record)
            def mutate(candidate):
                before=digest(candidate['domain']); previous=candidate['receipts'][-1]['digest'] if candidate['receipts'] else None
                for item in result['artifacts']:
                    manifest=item['manifest']; dependencies=[]
                    for blob in [{'path':manifest['path'],'source':item['source'],'sha256':manifest['sha256'],'bytes':manifest['bytes']}]+item['dependencies']:
                        target=self._artifact_path(blob['path']); target.parent.mkdir(parents=True,exist_ok=True)
                        if target.exists():
                            if hashlib.sha256(target.read_bytes()).hexdigest()!=blob['sha256']: raise ValidationError('Existing artifact hash mismatch')
                        else:
                            with open(blob['source'],'rb') as src, open(target,'xb') as dst:
                                while chunk:=src.read(65536): dst.write(chunk)
                                dst.flush(); os.fsync(dst.fileno())
                        if blob['path']!=manifest['path']:
                            dep={k:v for k,v in blob.items() if k!='source'}; dependencies.append(dep)
                            candidate['artifacts'][blob['path']]=dep
                    manifest['dependencies']=dependencies
                    candidate['artifacts'][manifest['path']]=copy.deepcopy(manifest)
                candidate['domain']=result['domain']; candidate['programs']=result['programs']; candidate['memory']=result['memory']
                candidate['outbox'].extend(result['outbox']); candidate['inbox'].extend(result['received'])
                receipt={'transaction_id':plan.request_id,'status':'committed','from_revision':candidate['revision'],
                    'to_revision':candidate['revision']+1,'before_domain_hash':before,'after_domain_hash':digest(result['domain']),
                    'plan_digest':digest(plan.commands),'contract':contract,'contract_digest':record['digest'],
                    'outputs':copy.deepcopy(result['outputs']),'previous_digest':previous,
                    'budget_usage':{'commands':len(plan.commands),'events':len(result['domain']['last_events'])}}
                receipt['digest']=content_digest(receipt); candidate['receipts'].append(receipt)
                candidate['runs'][plan.request_id]={'status':'committed','plan_digest':receipt['plan_digest'],'outputs':receipt['outputs']}
                for envelope in result['received']:
                    candidate['delivery_ledger'][envelope['message_id']]={'payload_digest':envelope['payload_digest'],'receipt':receipt['digest']}
                candidate['transition_log'].append({'request_id':plan.request_id,'contract':contract,
                    'commands':copy.deepcopy(plan.commands),'from_revision':candidate['revision'],
                    'before_domain_hash':before,'after_domain_hash':receipt['after_domain_hash'],
                    'artifact_hashes':[i['manifest']['sha256'] for i in result['artifacts']]})
                return receipt
            return self.repository.commit(plan.expected_revision,plan.expected_payload,plan.request_id,request_digest,mutate)

    def receive(self,envelope):
        _validate_envelope(envelope)
        if Path(envelope['destination']).resolve()!=self.path:
            raise ValidationError('Signal addressed to a different session path')
        # Delivery attempts/acks are transport metadata, never contract inputs.
        envelope={k:copy.deepcopy(v) for k,v in envelope.items() if k not in ('status','delivery_detail')}
        self.reload()
        plan=self.plan(request_id='receive:'+envelope['message_id'])
        plan._add('receive',envelope=copy.deepcopy(envelope))
        return self.commit(plan,contract='receive-signal-v1')
    def drain_outbox(self,max_messages=100):
        results=[]
        for envelope in self.snapshot()['outbox']:
            if envelope['status']=='acknowledged': continue
            if len(results)>=max_messages: break
            try:
                target=Runtime.open(envelope['destination'])
                receipt=target.receive(envelope)
                status='acknowledged'; detail=receipt['digest']
            except Exception as e:
                status='failed'; detail=f'{type(e).__name__}: {e}'
            def update(state,message_id=envelope['message_id'],status=status,detail=detail):
                entry=next(item for item in state['outbox'] if item['message_id']==message_id)
                entry['status']=status; entry['delivery_detail']=detail
            self.repository.metadata(update)
            results.append({'message_id':envelope['message_id'],'status':status,'detail':detail})
        return results
    def export_bundle(self,path):
        self.validate(); target=Path(path).resolve()
        if target.exists(): raise ValidationError('Export target already exists')
        with zipfile.ZipFile(target,'x',compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('session.json',canonical(self.snapshot()))
            for manifest in self.snapshot()['artifacts'].values(): archive.write(self._artifact_path(manifest['path']),manifest['path'])
        return str(target)
    def save_as(self,path):
        path=Path(path).resolve()
        if path.exists(): raise ValidationError('Save As target exists')
        state=self.snapshot(); self.validate()
        copies=[]
        for artifact in state['artifacts'].values():
            destination=(path.parent/artifact['path']).resolve()
            if not destination.is_relative_to(path.parent): raise ValidationError('Artifact traversal')
            if destination.exists() and (destination.stat().st_size!=artifact['bytes'] or hashlib.sha256(destination.read_bytes()).hexdigest()!=artifact['sha256']):
                raise ValidationError('Save As destination contains a conflicting artifact')
            copies.append((artifact,destination))
        for artifact,destination in copies:
            destination.parent.mkdir(parents=True,exist_ok=True)
            if not destination.exists(): destination.write_bytes(self._artifact_path(artifact['path']).read_bytes())
        repository=Repository(path); repository.create(state)
        return Runtime(repository)
    @classmethod
    def import_bundle(cls,bundle,path):
        path=Path(path).resolve()
        if path.exists(): raise ValidationError('Import target exists')
        with zipfile.ZipFile(bundle) as archive:
            if sum(i.file_size for i in archive.infolist())>256*1024*1024: raise BudgetExceeded('Bundle size budget')
            state=validate_document(strict_json(archive.read('session.json').decode('utf-8')))
            copies=[]
            for artifact in state['artifacts'].values():
                target=(path.parent/artifact['path']).resolve()
                if not target.is_relative_to(path.parent): raise ValidationError('Bundle traversal')
                data=archive.read(artifact['path'])
                if len(data)!=artifact['bytes'] or hashlib.sha256(data).hexdigest()!=artifact['sha256']: raise ValidationError('Invalid bundled artifact')
                if target.exists() and (target.stat().st_size!=artifact['bytes'] or hashlib.sha256(target.read_bytes()).hexdigest()!=artifact['sha256']):
                    raise ValidationError('Import destination contains a conflicting artifact')
                copies.append((target,data))
            for target,data in copies:
                target.parent.mkdir(parents=True,exist_ok=True)
                if not target.exists(): target.write_bytes(data)
            repository=Repository(path); repository.create(state)
        runtime=cls(repository); runtime.validate(); return runtime
    def replay(self,output_path):
        state=self.snapshot(); self.validate(); target=Path(output_path).resolve()
        if target.suffix!='.json': target=target/'replay-session.json'
        repository=Repository(target)
        repository.create(_initial_state(session_id=state['session_id'],domain=state['replay_checkpoint']['domain']))
        runtime=Runtime(repository); verified=[]
        for transition in state['transition_log']:
            if runtime.snapshot()['integrity']['domain_hash']!=transition['before_domain_hash']:
                raise ValidationError('Replay checkpoint/transition mismatch')
            original_revision=transition['from_revision']
            if runtime.revision!=original_revision:
                runtime.repository.metadata(lambda s:s.update(revision=original_revision-1))
            plan=runtime.plan(request_id=transition['request_id']); plan.commands=copy.deepcopy(transition['commands'])
            receipt=runtime.commit(plan,contract=transition['contract'])
            if receipt['after_domain_hash']!=transition['after_domain_hash']:
                raise ValidationError('Replay domain differs')
            hashes=[output['data']['sha256'] for output in receipt['outputs'] if output['type']=='artifact']
            if hashes!=transition['artifact_hashes']: raise ValidationError('Replay artifact bytes differ')
            verified.append(transition['request_id'])
        return {'status':'verified','transitions':len(verified),'domain_hash':runtime.snapshot()['integrity']['domain_hash'],
                'session':str(target),'live_delivery':False}

def _validate_ranks(payload):
    if not isinstance(payload,list) or len(payload)>10000:
        raise ValidationError('Rank stream must be a bounded list')
    for value in payload:
        if not isinstance(value,str) or not value.isascii() or not value.isdecimal() or len(value)>13000:
            raise ValidationError('Ranks must be bounded ASCII decimal strings')
        if len(value)>1 and value.startswith('0'): raise ValidationError('Canonical ranks cannot have leading zeros')

def _selectors_with_plane(source,plane):
    """A UI default affects bare compact terms; explicit directions always win."""
    import re
    from .sequences import parse_sequence
    if plane not in ('horizontal','vertical'): raise ValidationError('Unknown measurement plane')
    selectors=parse_sequence(source)
    if isinstance(source,str):
        tokens=[t for t in re.split(r'[\s+]+',source.strip()) if t]
        for selector,token in zip(selectors,tokens):
            if re.fullmatch(r'[vh](?:-distance)?(?:0|[1-9][0-9]*)',token): selector['plane']=plane
    else:
        for selector,original in zip(selectors,source):
            if 'plane' not in original and 'direction' not in original: selector['plane']=plane
    return selectors

def _validate_envelope(envelope):
    if not isinstance(envelope,dict) or envelope.get('protocol')!='pandr-signal-v2': raise ValidationError('Invalid signal protocol')
    canonical(envelope)
    for key in ('message_id','stream_id','source_session','source_domain_hash','destination','destination_layer','correlation_id'):
        if not isinstance(envelope.get(key),str) or not envelope[key]: raise ValidationError(f'Missing signal {key}')
    for key in ('sequence','source_revision','logical_tick','hop_count','max_hops'):
        if type(envelope.get(key)) is not int or envelope[key]<0: raise ValidationError(f'Invalid signal {key}')
    if envelope['hop_count']>=envelope['max_hops']: raise BudgetExceeded('Signal maximum hops reached')
    if envelope.get('codec')!='domalec-shortlex-repo-v1': raise ValidationError('Unsupported signal codec')
    if envelope.get('payload_digest')!=digest(envelope.get('payload')): raise ValidationError('Signal payload hash mismatch')
    if envelope.get('payload_type')=='rank-stream': _validate_ranks(envelope['payload'])
    elif envelope.get('payload_type') not in ('artifact-reference','events','text'): raise ValidationError('Unsupported signal payload')
