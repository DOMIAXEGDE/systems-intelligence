"""Controller persistence, failure recovery, inspection and trusted plugin contracts."""
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
import zipfile

from PIL import Image
from framelm.controller import Controller, get_controller
from framelm.controller import models
from framelm.controller.hooks import scope
from framelm.controller.visuals import all_entities, verify_bundle
from framelm.context_store import ContextStore, Cancelled
from framelm.chat import ChatService
from framelm.generator import Recipe


class ControllerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name); (self.root/'contexts').mkdir()
        self.config={'index_path':str(self.root/'state'/'app.db'),'context_dir':str(self.root/'contexts')}
        self.controller=get_controller(self.config)

    def tearDown(self):
        self.controller.close(); self.temp.cleanup()

    def dispatch(self,command,payload=None): return self.controller.dispatch(command,payload or {},str(uuid.uuid4()))
    def append(self,title='Example',body='Groups are mathematical objects.'):
        return self.dispatch('context.append',{'title':title,'body':body,'enabled':True,'tags':'math'})

    def test_typed_model_roundtrip_and_corruption(self):
        value={'z':'中文 😀\x00\n','a':(b'\x00\xff',False,None,-0.0,1.2345,2**200),'nested':{'type':'float64','hex':'not a marker'}}
        model=models.encode(value)
        restored=models.decode(model)
        self.assertEqual(list(restored),list(value)); self.assertEqual(restored,value)
        self.assertEqual(restored['a'][3].hex(),'-0x0.0p+0')
        self.assertTrue(all(b['length']<=256 for b in model['blocks']))
        broken=copy.deepcopy(model); broken['blocks'][0]['rank']='0'
        with self.assertRaises(ValueError): models.decode(broken)

    def test_dispatch_idempotency_and_service_observation(self):
        payload={'title':'Idempotent','body':'Hello'}
        a=self.controller.dispatch('context.append',payload,'same')
        self.assertEqual(self.controller.dispatch('context.append',payload,'same'),a)
        with self.assertRaises(ValueError): self.controller.dispatch('context.append',dict(payload,body='other'),'same')
        store=ContextStore(self.config['index_path'])
        store.toggle(a['id'],1,False)
        self.assertFalse(self.controller.inspect('context:'+a['id'])['entities'][0]['state']['enabled'])
        self.assertIn('context.toggle',{e['type'] for e in self.controller.journal.events()})

    def test_outbox_crash_duplicate_delivery_and_stale_revision(self):
        original=self.controller.journal.append
        def crash(name,*args,**kwargs):
            if kwargs.get('phase')=='committed': raise OSError('Simulated publication interruption')
            return original(name,*args,**kwargs)
        with patch.object(self.controller.journal,'append',side_effect=crash):
            with self.assertRaises(OSError): self.append()
        self.assertGreater(self.controller.publication_lag(),0)
        self.controller.flush(); state=self.controller.inspect({'kinds':['context']})['entities']; self.assertEqual(len(state),1)
        head=self.controller.journal.head()
        with self.controller.app_db() as db: db.execute('UPDATE controller_outbox SET published=0')
        self.controller.flush(); self.assertEqual(self.controller.journal.head(),head)
        item=state[0]['state']
        with self.assertRaises(ValueError):
            self.dispatch('context.toggle',{'id':item['id'],'revision':999,'enabled':False})

    def test_generator_rollback_retains_provisional_records(self):
        store=ContextStore(self.config['index_path']); cancel=threading.Event()
        with scope(self.controller,'cancelled-batch'):
            with self.assertRaises(Cancelled):
                store.append_generated(Recipe(mode='numeric',width=2,limit=10),cancel,lambda count:cancel.set())
        self.assertFalse(any(v['kind']=='context' for v in self.controller.inspect()['entities']))
        events=list(self.controller.journal.events())
        self.assertTrue(any(e['type']=='generator.record' and e['phase']=='provisional' for e in events))
        self.assertTrue(any(e['type']=='generator.rollback' and e['phase']=='cancelled' for e in events))
        self.assertFalse(any(e['type']=='context.created' for e in events))

    def test_historical_filters_complete_chat_and_replay_without_execution(self):
        a=self.append(); start=self.controller.journal.head()
        self.dispatch('context.toggle',{'id':a['id'],'revision':1,'enabled':False})
        self.assertEqual(self.controller.inspect({'enabled':True},start)['total'],1)
        self.assertEqual(self.controller.inspect({'enabled':True})['total'],0)
        self.controller.save_filter('enabled',{'kinds':['context'],'enabled':True})
        self.assertEqual(self.controller.inspect({'saved_filter':'enabled'},start)['total'],1)
        chat=self.dispatch('chat.create')
        store=ContextStore(self.config['index_path']); service=ChatService(store,self.controller.config)
        # Historical inspection must cover data beyond ChatService's display limit.
        from framelm.controller.hooks import outbox
        with store.connect() as db:
            for i in range(1,106):
                row={'session_id':chat['id'],'number':i,'request_id':str(uuid.uuid4()),'prompt':str(i),'topic':'math','result':{},'created_at':'2026-01-01T00:00:00Z'}
                db.execute('INSERT INTO chat_turns VALUES (?,?,?,?,?,?,?)',(chat['id'],i,row['request_id'],str(i),'math','{}',row['created_at']))
                outbox(db,'chat.turn','turn',f"{chat['id']}:{i}",row)
        self.controller.flush(); end=self.controller.journal.head()
        entities=all_entities(self.controller,['context:'+a['id'],'chat:'+chat['id']],end)
        self.assertEqual(sum(e['kind']=='turn' for e in entities),105)
        with (patch('framelm.core.Pipeline.run',side_effect=AssertionError('Replay called model')),
              patch('pandr.execution.ExecutionService.run_script',side_effect=AssertionError('Replay called script'))):
            frames=list(self.controller.replay('context:'+a['id'],start,end))
            self.assertTrue(frames)
            self.assertEqual(frames[0]['entities'][0]['state']['enabled'],True)

    def test_gif_bundle_and_branch(self):
        a=self.append(); start=self.controller.journal.head()
        self.dispatch('context.edit',{'id':a['id'],'revision':1,'title':'Changed','body':'More mathematics','enabled':True,'tags':'math'})
        end=self.controller.journal.head()
        exported=self.controller.export_replay('context:'+a['id'],start,end)
        manifest=verify_bundle(exported['bundle'])
        with Image.open(exported['gif']) as gif:
            self.assertEqual(gif.n_frames,exported['frames']); self.assertEqual(gif.size,(1000,700))
            for i in range(gif.n_frames): gif.seek(i); self.assertEqual(gif.info['duration'],200)
        self.assertEqual(manifest['sequences'],exported['sequences'])
        with self.assertRaises(ValueError): self.controller.export_replay(None,1,end,max_frames=1)
        branch=self.controller.branch(start,self.root/'branch')
        copied=ContextStore(branch['database']).get(a['id']); self.assertEqual(copied['title'],'Example')
        self.assertEqual(ContextStore(self.config['index_path']).get(a['id'])['title'],'Changed')
        self.assertTrue(branch['autonomy_paused'])

    def test_pandr_shared_commit_plot_and_receipt_recovery(self):
        result=self.dispatch('pandr.commit',{'revision':0,'commands':[{'op':'set_function','function':'sin(x)'}]})
        self.assertEqual(result['to_revision'],1)
        plot=self.dispatch('pandr.plot',{'x_range':[-4,4],'y_range':[-2,2]})
        self.assertTrue(plot['segments'])
        self.assertEqual(self.controller.inspect('pandr:main')['entities'][0]['state']['revision'],1)
        self.assertEqual(self.dispatch('pandr.codec',{'action':'decode','value':self.dispatch('pandr.codec',{'action':'encode','value':'Hello'})['value']})['value'],'Hello')
        export=Path(self.dispatch('pandr.export')); self.assertTrue(export.is_file())
        self.assertEqual(self.dispatch('pandr.replay')['status'],'verified')
        self.assertEqual(self.dispatch('pandr.deliver'),[])

    def plugin(self,source=None,capabilities=None,entity_scope=None,triggers=None):
        source=source or "result = controller.dispatch('context.append', {'title':'Skill context','body':'Created by a skill'})"
        draft=self.controller.skills.draft({'id':'test','version':'1.0','api_version':1,'skills':[{'id':'action','source':source,
            'capabilities':capabilities or ['context.append'],'scope':entity_scope or ['context:*'],'triggers':triggers or []}]})['skills'][0]
        self.controller.skills.activate(draft['id'],draft['digest']); return draft

    def test_supervision_activation_stale_approval_and_version_change(self):
        skill=self.plugin()
        result=self.controller.skills.run(skill['id'])
        self.assertEqual(result['status'],'success',result)
        self.assertEqual(self.controller.inspect({'kinds':['context']})['total'],0)
        proposal=self.controller.skills.list()['proposals'][0]
        approved=self.controller.skills.approve(proposal['id'],proposal['digest'])
        self.assertEqual(approved['status'],'completed',approved)
        self.assertEqual(self.controller.inspect({'kinds':['context']})['total'],1)
        self.controller.skills.run(skill['id']); pending=self.controller.skills.list()['proposals'][0]
        self.controller.skills.draft({'id':'test','version':'1.1','api_version':1,'skills':[{'id':'action','source':'result = 7','capabilities':[],'scope':[]}]})
        with self.assertRaises(ValueError): self.controller.skills.approve(pending['id'],pending['digest'])
        with self.assertRaises(ValueError): self.controller.skills.run(skill['id'])

    def test_grants_revocation_scope_timeout_and_cancellation(self):
        skill=self.plugin()
        self.controller.skills.grant(skill['id'],skill['digest'],['context.append'],['context:*'])
        result=self.controller.skills.run(skill['id']); self.assertEqual(result['status'],'success',result)
        self.assertEqual(self.controller.inspect({'kinds':['context']})['total'],1)
        self.controller.skills.revoke(skill['id']); self.controller.skills.run(skill['id'])
        self.assertEqual(self.controller.inspect({'kinds':['context']})['total'],1)
        denied=self.plugin("result=controller.dispatch('chat.create',{})")
        self.assertNotEqual(self.controller.skills.run(denied['id'])['status'],'success')
        slow=self.plugin('import time\ntime.sleep(10)')
        self.controller.skills.grant(slow['id'],slow['digest'],['context.append'],['context:*'],{'timeout':1})
        self.assertNotEqual(self.controller.skills.run(slow['id'])['status'],'success')
        results=[]
        worker=threading.Thread(target=lambda:results.append(self.controller.skills.run(slow['id'])))
        worker.start()
        deadline=time.monotonic()+3
        while slow['id'] not in self.controller.skills.running and time.monotonic()<deadline: time.sleep(.01)
        time.sleep(.2); self.controller.skills.cancel(slow['id']); worker.join(4)
        self.assertFalse(worker.is_alive()); self.assertNotEqual(results[0]['status'],'success')

    def test_subscription_dedup_and_no_self_recursion(self):
        skill=self.plugin(triggers=['context.created'])
        self.controller.skills.grant(skill['id'],skill['digest'],['context.append'],['context:*'])
        self.controller.skills.start(); self.append()
        deadline=time.monotonic()+6
        while self.controller.inspect({'kinds':['context']})['total']<2 and time.monotonic()<deadline: time.sleep(.05)
        self.assertEqual(self.controller.inspect({'kinds':['context']})['total'],2)
        event=next(e for e in self.controller.journal.events() if e['type']=='context.created')
        self.controller.skills.notify(event); time.sleep(.2)
        self.assertEqual(self.controller.inspect({'kinds':['context']})['total'],2)
        self.controller.skills.pause(); self.assertTrue(self.controller.skills.list()['paused'])

    def test_cli_and_registry(self):
        registry=self.controller.registry()
        names={r['name'] for r in registry['commands']}
        self.assertTrue({'context.append','chat.send','generator.generate','pandr.commit','controller.inspect'}<=names)
        self.assertTrue({'generator.record','backend.generated_byte','training.step'}<={e['name'] for e in registry['events']})
        (self.root/'configs').mkdir()
        config=self.root/'configs'/'demo.json'; config.write_text(json.dumps(self.config))
        result=subprocess.run([sys.executable,'-B','-m','framelm','--config',str(config),'controller','registry'],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(json.loads(result.stdout)['schema'],'controller-registry/1')


if __name__=='__main__': unittest.main()
