"""Locked JSON sessions, atomic commit, recovery, receipts and idempotency."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from datetime import datetime, timezone
from .canonical import canonical, digest, strict_json, content_digest
from .errors import ValidationError, RevisionConflict, RequestIdConflict, UnsupportedSchema

MAX_SESSION_BYTES = 32 * 1024 * 1024

def now():
    return datetime.now(timezone.utc).isoformat()

class FileLock:
    """Kernel-owned advisory byte lock; crashes release it, no stale deletion."""
    def __init__(self,path,timeout=10):
        self.path=Path(str(path)+'.lock'); self.timeout=timeout
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=open(self.path,'a+b')
        self.file.seek(0,2)
        if self.file.tell()==0:
            self.file.write(b'0'); self.file.flush()
        deadline=time.monotonic()+self.timeout
        while True:
            try:
                self.file.seek(0)
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                return self
            except OSError as e:
                if time.monotonic()>=deadline:
                    self.file.close()
                    raise RevisionConflict('Timed out acquiring session lock') from e
                time.sleep(.025)
    def __exit__(self,*args):
        try:
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_UN)
        finally:
            self.file.close()

def seal(state):
    state['integrity']['domain_hash']=digest(state['domain'])
    state['integrity']['receipt_head']=state['receipts'][-1]['digest'] if state['receipts'] else None
    state['integrity'].pop('payload_hash',None)
    state['integrity']['payload_hash']=digest(state)
    return state

def validate_document(state, verify_integrity=True):
    if not isinstance(state,dict):
        raise ValidationError('Session must be a JSON object')
    if type(state.get('schema_version')) is not int or state['schema_version']!=2:
        raise UnsupportedSchema('Expected session schema version 2; import/migrate explicitly')
    fields={'schema','schema_version','session_id','revision','engine','domain','programs','runs',
            'receipts','request_ledger','replay_checkpoint','transition_log','inbox','outbox',
            'delivery_ledger','artifacts','memory','ui','integrity','created_at','updated_at'}
    if set(state)!=fields:
        raise ValidationError('Session fields differ from schema')
    if state['schema']!='pandr-session-v2' or type(state['revision']) is not int or state['revision']<0:
        raise ValidationError('Invalid schema/revision')
    if not isinstance(state['session_id'],str) or not state['session_id']:
        raise ValidationError('Missing session ID')
    for field in ('programs','runs','request_ledger','delivery_ledger','artifacts','memory','ui','integrity'):
        if not isinstance(state[field],dict):
            raise ValidationError(f'{field} must be an object')
    for field in ('receipts','transition_log','inbox','outbox'):
        if not isinstance(state[field],list):
            raise ValidationError(f'{field} must be a list')
    validate_domain(state['domain'])
    checkpoint=state['replay_checkpoint']
    if not isinstance(checkpoint,dict) or set(checkpoint)!={'domain','domain_hash'} or digest(checkpoint['domain'])!=checkpoint['domain_hash']:
        raise ValidationError('Invalid replay checkpoint')
    validate_domain(checkpoint['domain'])
    previous=None
    for receipt in state['receipts']:
        if receipt.get('previous_digest')!=previous or receipt.get('digest')!=content_digest(receipt):
            raise ValidationError('Invalid receipt chain')
        previous=receipt['digest']
    for artifact in state['artifacts'].values():
        path=Path(artifact['path'])
        if path.is_absolute() or '..' in path.parts:
            raise ValidationError('Artifact path must stay inside session root')
    canonical(state)
    if len(canonical(state))>MAX_SESSION_BYTES:
        raise ValidationError('Session exceeds 32 MiB budget')
    if verify_integrity:
        candidate=copy.deepcopy(state)
        actual=candidate['integrity'].pop('payload_hash',None)
        if actual!=digest(candidate) or state['integrity'].get('domain_hash')!=digest(state['domain']):
            raise ValidationError('Session hash mismatch (including possible same-revision edit)')
        if state['integrity'].get('receipt_head')!=previous:
            raise ValidationError('Receipt head mismatch')
    return copy.deepcopy(state)

def validate_domain(domain):
    from .geometry import validate_points
    from .expressions import Function
    from .alphabets import validate_alphabet
    from .contracts import validate_contract
    required={'fabric','functions','geometry','layers','alphabets','selected_alphabet','contracts',
              'sequences','routes','budgets','tick','last_events','last_ranks','received'}
    if not isinstance(domain,dict) or set(domain)!=required:
        raise ValidationError('Invalid domain fields')
    fabric=domain['fabric']
    points=validate_points(fabric['points'])
    if points!=fabric['points'] or fabric['digest']!=content_digest(fabric):
        raise ValidationError('Invalid fabric identity/coordinates')
    if not isinstance(domain['functions'],dict) or 'main' not in domain['functions']:
        raise ValidationError('Main function required')
    for value in domain['functions'].values():
        Function.from_dict(value)
    geometry=domain['geometry']
    if geometry.get('profile')!='contacts-xy-v2' or geometry.get('root_policy') not in {
            'unique','all','leftmost','rightmost','nearest_contact','explicit_branch'}:
        raise ValidationError('Invalid geometry profile/root policy')
    from fractions import Fraction
    try:
        if len(geometry['window'])!=2 or Fraction(geometry['window'][0])>=Fraction(geometry['window'][1]):
            raise ValueError('window ordering')
    except (ValueError,KeyError,TypeError,ZeroDivisionError) as e:
        raise ValidationError('Invalid resolver window') from e
    layers=domain['layers']
    if not isinstance(layers,list) or not 1<=len(layers)<=17:
        raise ValidationError('There must be 1–17 registered layers')
    ids=set()
    allowed={'geometry','sequence','numeric','alphabet','text','image','audio','video','code','operator_extension'}
    for layer in layers:
        if not isinstance(layer,dict) or not isinstance(layer.get('id'),str) or not layer['id'] or layer['id'] in ids:
            raise ValidationError('Layer IDs must be unique')
        ids.add(layer['id'])
        if layer.get('type') not in allowed or type(layer.get('enabled')) is not bool:
            raise ValidationError('Invalid layer type/enabled')
        if layer['type']=='operator_extension':
            raise ValidationError('Register a supported built-in layer type; arbitrary extensions are not executable contracts')
    for key,alphabet in domain['alphabets'].items():
        validate_alphabet(alphabet)
        if key!=alphabet['id']:
            raise ValidationError('Alphabet registry key differs from ID')
    if domain['selected_alphabet'] not in domain['alphabets']:
        raise ValidationError('Selected alphabet is missing')
    for key,contract in domain['contracts'].items():
        validate_contract(contract)
        if key!=contract['id']:
            raise ValidationError('Contract registry key differs from ID')
    if type(domain['tick']) is not int or domain['tick']<0:
        raise ValidationError('Invalid logical tick')
    for name,value in domain['budgets'].items():
        if type(value) is not int or value<1:
            raise ValidationError(f'Positive integer budget required: {name}')
    if len(points)>domain['budgets']['max_points']:
        raise ValidationError('Point budget exceeded')
    if len(domain['last_events'])>domain['budgets']['max_events']:
        raise ValidationError('Event budget exceeded')
    for route in domain['routes']:
        if route.get('source_layer') not in ids or not isinstance(route.get('destination'),str):
            raise ValidationError('Invalid route')
    canonical(domain)

def atomic_write(path,state,backup=True):
    data=json.dumps(state,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')+b'\n'
    if len(data)>MAX_SESSION_BYTES:
        raise ValidationError('Serialized session exceeds the byte budget')
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    temp=None
    try:
        fd,temp=tempfile.mkstemp(prefix='.'+path.name, suffix='.tmp',dir=path.parent)
        with os.fdopen(fd,'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if backup and path.exists():
            bfd,btemp=tempfile.mkstemp(prefix='.'+path.name,suffix='.bak.tmp',dir=path.parent)
            try:
                with os.fdopen(bfd,'wb') as stream:
                    stream.write(path.read_bytes()); stream.flush(); os.fsync(stream.fileno())
                os.replace(btemp,Path(str(path)+'.bak'))
            finally:
                if os.path.exists(btemp): os.unlink(btemp)
        os.replace(temp,path); temp=None
    finally:
        if temp and os.path.exists(temp): os.unlink(temp)

class Repository:
    def __init__(self,path):
        self.path=Path(path).resolve(); self.lock=threading.RLock(); self.state=None
    def _read(self):
        if self.path.stat().st_size>MAX_SESSION_BYTES:
            raise ValidationError('Session exceeds byte budget')
        return validate_document(strict_json(self.path.read_text(encoding='utf-8')))
    def create(self,state):
        with self.lock, FileLock(self.path):
            if self.path.exists(): raise ValidationError('Session already exists')
            candidate=seal(copy.deepcopy(state)); validate_document(candidate)
            atomic_write(self.path,candidate,backup=False); self.state=candidate
        return self.snapshot()
    def load(self,recover=True):
        with self.lock, FileLock(self.path):
            try:
                self.state=self._read()
            except UnsupportedSchema:
                raise
            except (ValidationError,UnicodeError) as original:
                backup=Path(str(self.path)+'.bak')
                if not recover or not backup.exists(): raise
                restored=validate_document(strict_json(backup.read_text(encoding='utf-8')))
                quarantine=self.path.with_name(self.path.name+f'.corrupt-{time.time_ns()}')
                shutil.copyfile(self.path,quarantine)
                restored['ui']['recovery']={'quarantine':quarantine.name,'reason':str(original)}
                restored['revision']+=1; restored['updated_at']=now(); seal(restored)
                atomic_write(self.path,restored,backup=False); self.state=restored
        return self.snapshot()
    def snapshot(self):
        with self.lock: return copy.deepcopy(self.state)
    def commit(self,expected_revision,expected_payload,request_id,request_digest,mutate):
        with self.lock, FileLock(self.path):
            current=self._read()
            prior=current['request_ledger'].get(request_id)
            if prior:
                if prior['request_digest']!=request_digest:
                    raise RequestIdConflict('Request ID already used for different inputs')
                self.state=current
                return copy.deepcopy(prior['receipt'])
            if current['revision']!=expected_revision or current['integrity']['payload_hash']!=expected_payload:
                raise RevisionConflict('Session changed; reload and replan')
            candidate=copy.deepcopy(current)
            receipt=mutate(candidate)
            if candidate['domain']['fabric']!=current['domain']['fabric']:
                if candidate['domain']['fabric']['id'] == current['domain']['fabric']['id']:
                    raise ValidationError('A transaction cannot modify a fabric in place; it must assign a new fabric ID')
            candidate['revision']=current['revision']+1; candidate['updated_at']=now()
            candidate['request_ledger'][request_id]={'request_digest':request_digest,'receipt':receipt}
            seal(candidate); validate_document(candidate)
            atomic_write(self.path,candidate); self.state=candidate
            return copy.deepcopy(receipt)
    def metadata(self,mutate):
        with self.lock, FileLock(self.path):
            candidate=self._read(); mutate(candidate)
            candidate['revision']+=1; candidate['updated_at']=now(); seal(candidate)
            validate_document(candidate); atomic_write(self.path,candidate); self.state=candidate
        return self.snapshot()
