"""Exercise real registered handlers through Python, CLI and the browser HTTP API."""
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

from framelm.core import DEFAULTS
from framelm.studio import make_server
from framelm.__main__ import main


class RealInterfaceTests(unittest.TestCase):
    def test_all_registered_application_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary); (root/'contexts').mkdir()
            config=DEFAULTS|{'index_path':str(root/'state'/'app.db'),'context_dir':str(root/'contexts')}
            config_path=root/'config.json'; config_path.write_text(json.dumps(config))
            server=make_server(config,0); worker=threading.Thread(target=server.serve_forever,daemon=True); worker.start()
            c=server.app.controller; base=f'http://127.0.0.1:{server.server_port}/api/controller/dispatch'
            required={v['name'] for v in c.registry()['commands']}
            try:
                for surface in ('python','cli','http'):
                    exercised=set()
                    def call(name,payload=None,fail=False,request_id=None):
                        exercised.add(name); request_id=request_id or str(uuid.uuid4())
                        data={'command':name,'payload':payload or {},'request_id':request_id}
                        with self.subTest(surface=surface,command=name,expected_failure=fail):
                            if surface=='python':
                                if fail:
                                    with self.assertRaises((ValueError,RuntimeError,OSError)): c.dispatch(name,payload or {},request_id)
                                    return None
                                return c.dispatch(name,payload or {},request_id)
                            if surface=='http':
                                request=Request(base,data=json.dumps(data).encode(),headers={'Content-Type':'application/json','X-Studio-Token':server.app.token})
                                if fail:
                                    with self.assertRaises(HTTPError) as error: urlopen(request)
                                    self.assertIn(error.exception.code,(400,409)); error.exception.close(); return None
                                with urlopen(request) as response: return json.load(response)
                            output=io.StringIO(); error=io.StringIO()
                            with patch('sys.argv',['framelm','--config',str(config_path),'controller','dispatch','--data',json.dumps(data)]), patch.object(c,'close'), redirect_stdout(output), redirect_stderr(error):
                                status=main()
                            self.assertEqual(status,2 if fail else 0,error.getvalue())
                            return None if fail else json.loads(output.getvalue())

                    row_request=str(uuid.uuid4())
                    row=call('context.append',{'title':'Groups '+surface,'body':'A group has an identity and inverses.','enabled':True},request_id=row_request)
                    self.assertEqual(call('context.get',{'id':row['id']})['body'],row['body'])
                    call('context.list')
                    row=call('context.edit',{'id':row['id'],'revision':1,'title':row['title'],'body':row['body']+' Group symmetry is useful.'})
                    row=call('context.toggle',{'id':row['id'],'revision':row['revision'],'enabled':False})
                    row=call('context.toggle',{'id':row['id'],'revision':row['revision'],'enabled':True})
                    (root/'contexts'/(surface+'.txt')).write_text('An abelian group has a commutative operation.')
                    self.assertGreaterEqual(call('context.adopt',{'directory':str(root/'contexts')})['added'],1)
                    self.assertIn('Groups',call('context.export')['jsonl'])
                    call('index.build')
                    chat=call('chat.create'); call('chat.list'); call('chat.get',{'id':chat['id']})
                    call('chat.send',{'id':chat['id'],'revision':chat['revision'],'prompt':'What is a group?','request_id':str(uuid.uuid4())})
                    self.assertEqual(call('pipeline.run',{'prompt':'What is a group?','local_only':True})['status'],'resolved')
                    recipe={'mode':'numeric','width':2,'limit':2}
                    call('generator.plan',{'recipe':recipe}); call('generator.preview',{'recipe':recipe})
                    call('generator.generate',{'recipe':recipe}); call('generator.cancel')
                    selected={'ids':['context:'+row['id']]}
                    inspected=call('controller.inspect',{'selection':selected,'include_models':True})
                    self.assertEqual(inspected['total'],1)
                    position=inspected['at_sequence']
                    call('controller.events',{'selection':selected}); call('controller.plot',{'selection':selected})
                    call('controller.reconcile',{'request_id':row_request,'command':'context.append'})
                    call('controller.filter.save',{'name':surface,'selection':selected})
                    call('controller.view.publish',{'name':surface,'definition':{'source_fields':['context.revision'],'units':'revision','series':[{'label':'revision','points':[[0,1],[1,2]]}]}})
                    exported=call('controller.export',{'selection':selected,'start_sequence':position,'end_sequence':position,'format':'bundle'})
                    self.assertTrue(Path(exported['bundle']).is_file())

                    pr=call('pandr.inspect')
                    call('pandr.commit',{'revision':pr['revision'],'commands':[{'op':'set_function','function':'x'},{'op':'save_script','id':surface,'source':"print('Managed script receipt')"}]})
                    self.assertIsInstance(call('pandr.measure'),list)
                    self.assertIsInstance(call('pandr.resolve',{'sequence':'v17'}),list)
                    self.assertTrue(call('pandr.plot',{'functions':'sin(x)'})['segments'])
                    call('pandr.codec',{'action':'encode','value':'Hello'})
                    call('pandr.alphabet',{'action':'create','options':{'count':2}})
                    self.assertEqual(call('pandr.execute',{'identifier':surface})['status'],'success')
                    self.assertEqual(call('pandr.deliver'),[])
                    self.assertTrue(Path(call('pandr.export')).is_file())
                    self.assertEqual(call('pandr.replay')['status'],'verified')

                    call('skill.list')
                    skill=call('skill.draft',{'manifest':{'id':'interface-'+surface,'version':'1.0','api_version':1,'skills':[{'id':'add','source':"result=controller.dispatch('context.append',{'title':'Plugin context','body':'Supervised content'})",'capabilities':['context.append'],'scope':['context:*']}]}})['skills'][0]
                    key={'id':skill['id'],'digest':skill['digest']}
                    call('skill.activate',key)
                    call('skill.run',{'id':skill['id']})
                    proposal=c.skills.list()['proposals'][0]
                    self.assertEqual(call('proposal.approve',{'id':proposal['id'],'digest':proposal['digest']})['status'],'completed')
                    call('plugin.'+skill['id']+'.run')
                    proposal=c.skills.list()['proposals'][0]
                    call('proposal.reject',{'id':proposal['id']})
                    call('skill.grant',key|{'capabilities':['context.append'],'scope':['context:*']})
                    self.assertEqual(call('skill.run',{'id':skill['id']})['result']['status'],'executed')
                    call('skill.pause',{'paused':True}); call('skill.pause',{'paused':False})
                    call('skill.revoke',{'id':skill['id']}); call('skill.cancel',{'id':skill['id']}); call('skill.disable',{'id':skill['id']})
                    call('controller.branch',{'at_sequence':c.journal.head(),'destination':str(root/('branch-'+surface))})
                    # Missing held-out data is a real, observed training failure, without training a model.
                    call('training.run',{'data':str(root/'missing.jsonl'),'validation':str(root/'missing-validation.jsonl')},fail=True)
                    call('context.get',{'id':'missing'},fail=True)
                    call('generator.preview',{'recipe':{'mode':'unknown'}},fail=True)
                    self.assertTrue(required<=exercised,required-exercised)
            finally: server.shutdown(); server.server_close(); worker.join()


if __name__=='__main__': unittest.main()
