"""SDK imported by approved, trusted plugin scripts."""
import json
import os
from pathlib import Path
from urllib.request import Request, urlopen


class Client:
    def __init__(self):
        self.url=os.environ['CONTROLLER_BROKER']
        self.token=os.environ['CONTROLLER_TOKEN']
        self.input=json.loads(Path(os.environ['CONTROLLER_INPUT']).read_text(encoding='utf-8'))

    def _call(self, operation, **data):
        request=Request(self.url+'/'+operation,data=json.dumps(data,ensure_ascii=False).encode(),
                        headers={'Content-Type':'application/json','Authorization':'Bearer '+self.token})
        with urlopen(request,timeout=30) as response: result=json.load(response)
        if 'error' in result: raise RuntimeError(result['error'])
        return result

    def inspect(self, selection=None, at_sequence=None, offset=0, limit=100):
        return self._call('inspect',selection=selection,at_sequence=at_sequence,offset=offset,limit=limit)

    def iter_entities(self, selection=None, at_sequence=None):
        offset=0
        while True:
            page=self.inspect(selection,at_sequence,offset,1000)
            at_sequence=page['at_sequence']
            yield from page['entities']
            if page['next_offset'] is None: return
            offset=page['next_offset']

    def dispatch(self, command, payload, request_id=None, expected_revision=None):
        return self._call('dispatch',command=command,payload=payload,request_id=request_id,expected_revision=expected_revision)

    def emit(self, event, payload): return self._call('emit',event=event,payload=payload)
    def publish_view(self, name, definition): return self._call('view',name=name,definition=definition)
    def complete(self, result): return self._call('result',result=result)
