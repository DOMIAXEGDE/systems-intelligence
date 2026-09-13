import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen
from framelm.chat import ChatService
from framelm.context_store import ContextStore, ConflictError
from framelm.core import DEFAULTS
from framelm.studio import make_server


class ChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); (self.root/'contexts').mkdir()
        self.cfg = DEFAULTS | {'index_path':str(self.root/'test.db'),'context_dir':str(self.root/'contexts')}
        self.store = ContextStore(self.cfg['index_path'])
        self.context = self.store.append({'title':'Abelian groups','body':'An abelian group has a commutative operation.'})
        self.chat = ChatService(self.store,self.cfg)
        self.session = self.chat.create()

    def send(self,prompt,session=None,key='request-0001'):
        s = session or self.session
        return self.chat.send(s['id'],prompt,s['revision'],key)

    def test_grounded_reply_persists_with_sources(self):
        s = self.send('What is an abelian group?')
        self.assertIn('commutative',s['turns'][0]['result']['response'])
        self.assertTrue(s['turns'][0]['result']['contexts'])
        reopened = ChatService(ContextStore(self.cfg['index_path']),self.cfg)
        self.assertEqual(reopened.get(s['id'])['turns'],s['turns'])

    def test_followup_uses_topic_and_fresh_context(self):
        s = self.send('What is an abelian group?')
        s = self.send('Tell me more',s,'request-0002')
        r = s['turns'][-1]['result']
        self.assertTrue(r['conversation']['used_previous_topic'])
        self.assertEqual(r['status'],'resolved')

    def test_disabled_source_not_reused_from_history(self):
        s = self.send('What is an abelian group?')
        self.store.toggle(self.context['id'],self.context['revision'],False)
        s = self.send('Tell me more',s,'request-0002')
        r = s['turns'][-1]['result']
        self.assertEqual(r['status'],'insufficient_context')
        self.assertEqual(r['contexts'],[])
        self.assertIn('commutative',s['turns'][0]['result']['response'])

    def test_new_topic_replaces_previous_topic(self):
        self.store.append({'title':'Sequence','body':'A sequence is an indexed family of elements.'})
        s = self.send('What is an abelian group?')
        s = self.send('What is a sequence?',s,'request-0002')
        self.assertFalse(s['turns'][-1]['result']['conversation']['used_previous_topic'])
        self.assertIn('indexed family',s['turns'][-1]['result']['response'])

    def test_idempotent_retry_and_conflicting_request(self):
        self.send('abelian group')
        s = self.send('abelian group')
        self.assertEqual(s['revision'],1)
        self.assertEqual(len(s['turns']),1)
        with self.assertRaises(ConflictError): self.send('different prompt')

    def test_stale_revision_rejected(self):
        self.send('abelian group')
        with self.assertRaises(ConflictError): self.send('sequence',key='request-0002')

    def test_invalid_prompt_not_saved(self):
        with self.assertRaises(ValueError): self.send(' ')
        self.assertEqual(self.chat.get(self.session['id'])['revision'],0)

    def test_sessions_isolated(self):
        self.send('abelian group')
        other = self.chat.create()
        self.assertEqual(self.send('Tell me more',other)['turns'][0]['result']['status'],'insufficient_context')

    def test_backend_failure_does_not_save_partial_turn(self):
        with patch('framelm.chat.Pipeline.run',side_effect=RuntimeError('Model unavailable')):
            with self.assertRaises(RuntimeError): self.send('abelian group')
        self.assertEqual(self.chat.get(self.session['id'])['revision'],0)

    def test_configured_backend_is_used(self):
        cfg = self.cfg | {'backend':'ollama','model':'test-model'}
        service = ChatService(self.store,cfg)
        with patch('framelm.chat.Pipeline') as pipeline:
            pipeline.return_value.run.return_value = {'response':'Answer','contexts':[]}
            service.send(self.session['id'],'abelian group',0,'request-0001')
            self.assertEqual(pipeline.call_args.args[0]['backend'],'ollama')

    def test_http_chat_roundtrip(self):
        server = make_server(self.cfg,0)
        worker = threading.Thread(target=server.serve_forever,daemon=True); worker.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            def post(path,data):
                req = Request(base+path,data=json.dumps(data).encode(),headers={'X-Studio-Token':server.app.token})
                with urlopen(req) as r: return json.load(r)
            s = post('/api/chat/new',{})
            s = post('/api/chat/send',{'id':s['id'],'revision':0,'prompt':'abelian group','request_id':'request-0001'})
            self.assertEqual(s['turns'][0]['result']['status'],'resolved')
            with urlopen(base+'/api/chat?id='+s['id']) as r:
                self.assertEqual(json.load(r)['revision'],1)
            with urlopen(base+'/api/chats') as r:
                self.assertEqual(json.load(r)['backend'],'extractive')
        finally:
            server.shutdown(); server.server_close(); worker.join()


if __name__ == '__main__': unittest.main()
