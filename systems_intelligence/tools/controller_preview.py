"""Disposable local UI fixture; never opens the user's application database."""
from pathlib import Path
import argparse
import uuid
from framelm.core import DEFAULTS
from framelm.studio import make_server


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--port',type=int,default=8770); args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]/'.controller-preview'
    root.mkdir(exist_ok=True); (root/'contexts').mkdir(exist_ok=True)
    cfg=DEFAULTS | {'index_path':str(root/'contexts.sqlite3'),'context_dir':str(root/'contexts'),'backend':'extractive'}
    server=make_server(cfg,args.port)
    c=server.app.controller
    if not c.inspect({'kinds':['context']})['total']:
        item=c.dispatch('context.append',{'title':'Groups and symmetry','body':'A group is a set with an associative operation, an identity, and inverses. Symmetry transformations form groups.','enabled':True,'tags':'mathematics'},str(uuid.uuid4()))
        c.dispatch('context.edit',{'id':item['id'],'revision':1,'title':'Groups and symmetry','body':item['body']+' An abelian group has a commutative operation.','enabled':True,'tags':'mathematics'},str(uuid.uuid4()))
        chat=c.dispatch('chat.create',{},str(uuid.uuid4()))
        c.dispatch('chat.send',{'id':chat['id'],'revision':0,'prompt':'What is a group?','request_id':str(uuid.uuid4())},str(uuid.uuid4()))
        c.dispatch('generator.generate',{'recipe':{'mode':'numeric','width':2,'limit':3}},str(uuid.uuid4()))
    print(f'Controller preview: http://127.0.0.1:{server.server_port}',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()


if __name__=='__main__': main()
