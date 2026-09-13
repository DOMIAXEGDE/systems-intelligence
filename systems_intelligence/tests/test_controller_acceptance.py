"""Cross-interface coverage and failure-injection acceptance checks."""
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

from framelm.controller import get_controller
from framelm.controller import models
from framelm.controller.visuals import all_entities, verify_bundle
from framelm.context_store import ContextStore
from framelm.core import DEFAULTS, Pipeline
from framelm.studio import make_server


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)
        (self.root/'contexts').mkdir()
        self.config=DEFAULTS|{'index_path':str(self.root/'state'/'app.db'),'context_dir':str(self.root/'contexts')}
        self.c=get_controller(self.config)

    def tearDown(self): self.c.close(); self.temp.cleanup()

    def dispatch(self,name,payload=None,request_id=None): return self.c.dispatch(name,payload or {},request_id or str(uuid.uuid4()))

    def test_committed_request_and_pandr_crash_reconcile_without_repeating(self):
        original=self.c.journal.append
        def crash(name,*a,**kw):
            if kw.get('phase')=='committed': raise OSError('Injected journal failure')
            return original(name,*a,**kw)
        payload={'title':'Committed once','body':'Preserved'}
        with patch.object(self.c.journal,'append',side_effect=crash):
            with self.assertRaises(OSError): self.dispatch('context.append',payload,'crash')
        result=self.dispatch('context.append',payload,'crash')
        self.assertEqual(result['status'],'committed_pending_result')
        self.assertEqual(self.c.inspect({'kinds':['context']})['total'],1)
        self.c.pandr
        operation={'revision':0,'commands':[{'op':'set_function','function':'sin(x)'}]}
        with patch.object(self.c.journal,'append',side_effect=crash):
            with self.assertRaises(OSError): self.dispatch('pandr.commit',operation,'pandr-crash')
        recovered=self.dispatch('pandr.commit',operation,'pandr-crash')
        self.assertEqual(recovered['status'],'committed'); self.assertEqual(self.c.pandr.revision,1)

    def test_uncertain_external_effect_never_repeats_and_event_cannot_execute(self):
        calls=[]
        def unknown(p):
            calls.append(p); raise OSError('Connection lost after sending request')
        self.c.register('plugin.example.external',unknown,external=True)
        with self.assertRaises(OSError): self.dispatch('plugin.example.external',{},'external')
        self.assertEqual(self.dispatch('plugin.example.external',{},'external')['status'],'indeterminate')
        self.c.emit('plugin.example.external',{'historical':True})
        self.assertEqual(len(calls),1)

    def test_streamed_chunks_and_incomplete_stream_failure(self):
        from framelm.backends import OllamaBackend
        from framelm.core import frame_prompt, Context
        from framelm.controller.hooks import scope
        stream=b'{"message":{"content":"Hello "},"done":false}\n{"message":{"content":"world"},"done":false}\n{"done":true}\n'
        cfg=self.config|{'stream_output':True,'model':'fixture','candidate_count':1}
        with scope(self.c), patch('framelm.backends.urlopen',return_value=io.BytesIO(stream)):
            result=OllamaBackend().generate(frame_prompt('hello'),[Context(1,'test','Hello',1)],cfg)
        self.assertEqual(result[0].text,'Hello world')
        chunks=[e['outcome'] for e in self.c.journal.events() if e['type']=='backend.output_chunk']
        self.assertEqual([c['text'] for c in chunks],['Hello ','world'])
        self.assertTrue(all(c['granularity']=='chunk' and c['token_detail_available'] is False for c in chunks))
        with patch('framelm.backends.urlopen',return_value=io.BytesIO(stream.splitlines()[0]+b'\n')):
            with self.assertRaises(RuntimeError): OllamaBackend().generate(frame_prompt('hello'),[],cfg)

    def test_historical_generator_filter_includes_provisional_events(self):
        self.dispatch('generator.generate',{'recipe':{'mode':'numeric','width':2,'limit':3}})
        selected=self.c.events({'kinds':['generator']},limit=1000)
        self.assertTrue(any(e['type']=='generator.record' and e['phase']=='provisional' for e in selected['events']))
        artifact=self.c.export_replay({'kinds':['generator']},1,selected['at_sequence'],format='bundle',include_lifecycle=True)
        self.assertGreater(artifact['frames'],3)
        verify_bundle(artifact['bundle'])

    def test_large_group_replay_and_branch_preserve_non_application_entities(self):
        changes=[{'kind':'context','id':str(i),'state':{'title':'Unicode Ω😀','body':'数据','enabled':True,'revision':i}} for i in range(1007)]
        self.c.emit('test.baseline',phase='baseline',changes=changes)
        head=self.c.journal.head()
        with patch.object(Pipeline,'run',side_effect=AssertionError('Must not execute during replay')):
            frame=next(self.c.replay({'kinds':['context']},head,head))
        self.assertEqual(len(frame['entities']),1007)
        self.assertEqual({e['model'] for e in frame['entities']},{e['model'] for e in all_entities(self.c,{'kinds':['context']},head)})

    def test_branch_preserves_index_skill_generator_and_view(self):
        item=self.dispatch('context.append',{'title':'Branch reference','body':'Branch identity stays stable.'})
        generation=self.dispatch('generator.generate',{'recipe':{'mode':'numeric','width':2,'limit':2}})
        skill=self.c.skills.draft({'id':'branch','version':'1','api_version':1,'skills':[{'id':'read','source':'result = 1','scope':['context:*']}]})['skills'][0]
        self.c.skills.activate(skill['id'],skill['digest'])
        self.c.publish_view({'name':'example','definition':{'source_fields':['context.revision'],'units':'revision','series':[{'label':'revision','points':[[0,1],[1,2]]}]}})
        seq=self.c.journal.head()
        with self.c.app_db() as db: chunks=db.execute('SELECT rowid,source,text FROM chunks ORDER BY rowid').fetchall()
        branch=self.c.branch(seq,self.root/'branch')
        child=get_controller(dict(self.config,index_path=branch['database'],context_dir=str(self.root/'branch'/'contexts')))
        try:
            with child.app_db() as db: self.assertEqual(chunks,db.execute('SELECT rowid,source,text FROM chunks ORDER BY rowid').fetchall())
            self.assertEqual(child.inspect('generator:'+generation['batch_id'])['total'],1)
            self.assertFalse(child.skills.list()['skills'][0]['enabled'])
            self.assertIsNone(child.skills.list()['skills'][0]['grant'])
            self.assertEqual(child.plot(view='view:example')['series'][0]['points'],[[0,1],[1,2]])
            self.assertEqual(child.inspect('context:'+item['id'])['entities'][0]['state'],self.c.inspect('context:'+item['id'],seq)['entities'][0]['state'])
        finally: child.close()

    def test_stale_skill_proposal_scope_pagination_and_budgets(self):
        item=self.dispatch('context.append',{'title':'Original','body':'Original body'})
        source=f"result=controller.dispatch('context.toggle',{{'id':{item['id']!r},'enabled':False}})"
        s=self.c.skills.draft({'id':'stale','version':'1','api_version':1,'skills':[{'id':'edit','source':source,'scope':['context:*'],'capabilities':['context.toggle']}]})['skills'][0]
        self.c.skills.activate(s['id'],s['digest']); self.c.skills.run(s['id'])
        proposal=self.c.skills.list()['proposals'][0]
        self.dispatch('context.toggle',{'id':item['id'],'revision':1,'enabled':True})
        result=self.c.skills.approve(proposal['id'],proposal['digest'])
        self.assertEqual(result['status'],'failed'); self.assertTrue(ContextStore(self.config['index_path']).get(item['id'])['enabled'])
        self.c.skills.grant(s['id'],s['digest'],['context.toggle'],['context:'+item['id']],{'max_actions':1,'max_depth':1})
        with self.assertRaises(ValueError): self.c.skills.run(s['id'],event={'depth':1})
        run={'id':s['id'],'digest':s['digest'],'actions':0,'budgets':{'max_actions':1},'correlation':'budget','cause':None,'depth':1}
        page=self.c.skills._broker_call(run,'/inspect',{'selection':{'kinds':['context']},'offset':0,'limit':1})
        self.assertEqual(page['total'],1); self.assertEqual(page['entities'][0]['kind'],'context')
        self.c.skills._broker_call(run,'/dispatch',{'command':'context.toggle','payload':{'id':item['id'],'enabled':True}})
        with self.assertRaises(ValueError): self.c.skills._broker_call(run,'/dispatch',{'command':'context.toggle','payload':{'id':item['id'],'enabled':True}})

    def test_skill_state_failure_is_atomic_and_interrupted_approval_is_visible(self):
        from framelm.controller.skills import Skills
        service=self.c.skills
        skill=service.draft({'id':'atomic','version':'1','api_version':1,'skills':[{'id':'add','source':"result=controller.dispatch('context.append',{'title':'Atomic','body':'Kept'})",'scope':['context:*'],'capabilities':['context.append']}]})['skills'][0]
        with patch('framelm.controller.models.encode',side_effect=OSError('Crash during model storage')):
            with self.assertRaises(OSError): service.activate(skill['id'],skill['digest'])
        self.assertFalse(service._get(skill['id'])['enabled'])
        service.activate(skill['id'],skill['digest']); service.run(skill['id'])
        proposal=service.list()['proposals'][0]; proposal['status']='executing'
        service._record_proposal('test.interrupted_approval',proposal,'started')
        recovered=Skills(self.c)
        try: self.assertEqual(recovered._proposal(proposal['id'])['status'],'indeterminate')
        finally: recovered.close()
        self.assertEqual(self.c.inspect({'kinds':['context']})['total'],0)

    def test_shared_cancellation_and_subscription_owner_lock(self):
        from framelm.controller import Controller
        from pandr.errors import RevisionConflict
        self.c.skills.start()
        reader=Controller(self.config)
        try:
            with self.assertRaises(RevisionConflict): reader.skills.start()
            reader.cancel_generation()
            self.assertTrue(self.c.cancel.is_set())
            self.c.cancel.clear()
            reader.close()
            self.assertFalse(self.c.cancel.is_set())
        finally: reader.close()

    def test_concurrent_chat_requests_publish_only_one_revision(self):
        self.dispatch('context.append',{'title':'Groups','body':'A group has an identity and inverses.'})
        chat=self.dispatch('chat.create'); barrier=threading.Barrier(2); results=[]; original=Pipeline.run
        def synchronized(*a,**kw): barrier.wait(timeout=10); return original(*a,**kw)
        def submit():
            try: results.append(self.dispatch('chat.send',{'id':chat['id'],'revision':0,'prompt':'What is a group?','request_id':str(uuid.uuid4())}))
            except ValueError as exc: results.append(exc)
        with patch.object(Pipeline,'run',side_effect=None,autospec=True) as mocked:
            mocked.side_effect=synchronized
            workers=[threading.Thread(target=submit) for _ in range(2)]
            for worker in workers: worker.start()
            for worker in workers: worker.join(15); self.assertFalse(worker.is_alive())
        self.assertEqual(sum(isinstance(r,ValueError) for r in results),1)
        self.assertEqual(self.c.inspect({'kinds':['turn']})['total'],1)

    def test_registry_routes_all_commands_through_python_cli_and_http(self):
        """Transport coverage uses probe handlers; behavior is tested separately above."""
        from framelm.__main__ import main
        server=make_server(self.config,0); worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
        base=f'http://127.0.0.1:{server.server_port}'
        config_file=self.root/'config.json'; config_file.write_text(json.dumps(self.config))
        try:
            registry=self.c.registry()
            for definition in registry['commands']:
                name=definition['name']
                with self.subTest(command=name):
                    original=self.c._handlers[name]
                    original_output_schema=self.c._commands[name]['output_schema']
                    self.c._commands[name]['output_schema']={}
                    self.c._handlers[name]=(lambda p:{'probe':p},lambda p:p)
                    try:
                        self.assertEqual(self.dispatch(name,{'entry':'python'})['probe']['entry'],'python')
                        request=Request(base+'/api/controller/dispatch',data=json.dumps({'command':name,'payload':{'entry':'http'}}).encode(),headers={'Content-Type':'application/json','X-Studio-Token':server.app.token})
                        with urlopen(request) as response: self.assertEqual(json.load(response)['probe']['entry'],'http')
                        output=io.StringIO()
                        with patch('sys.argv',['framelm','--config',str(config_file),'controller','dispatch','--data',json.dumps({'command':name,'payload':{'entry':'cli'}})]), patch.object(self.c,'close'), redirect_stdout(output):
                            self.assertEqual(main(),0)
                        self.assertEqual(json.loads(output.getvalue())['probe']['entry'],'cli')
                    finally:
                        self.c._handlers[name]=original
                        self.c._commands[name]['output_schema']=original_output_schema
            with self.assertRaises(HTTPError) as error:
                urlopen(Request(base+'/api/controller/dispatch',data=b'{}',headers={'Content-Type':'application/json'}))
            self.assertEqual(error.exception.code,403); error.exception.close()
            for failed_name,failed_payload in [('context.get',{'id':'missing'}),('generator.preview',{'recipe':{'mode':'invalid'}})]:
                with self.assertRaises(ValueError): self.dispatch(failed_name,failed_payload)
            self.assertEqual(self.dispatch('generator.cancel')['status'],'cancellation_requested')
        finally: server.shutdown(); server.server_close(); worker.join()


if __name__=='__main__': unittest.main()
