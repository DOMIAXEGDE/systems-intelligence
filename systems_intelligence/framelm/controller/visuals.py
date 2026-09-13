"""Linked plots, verified replay bundles, GIF animation and isolated branching."""
from contextlib import closing
import io
import json
import math
from pathlib import Path
import sqlite3
import uuid
import zipfile

from . import models


def all_entities(controller, selection, sequence):
    offset = 0; values = []
    while True:
        page = controller._inspect(selection, sequence, offset=offset, limit=1000)
        values.extend(page['entities'])
        if page['next_offset'] is None: return values
        offset = page['next_offset']


def plot_state(controller, selection, at_sequence, view, offset=0, limit=100):
    page = controller._inspect(selection, at_sequence, offset=offset, limit=limit)
    series = []
    definitions=controller.view_definitions(page['at_sequence'])
    if view not in {v['id'] for v in definitions}: raise ValueError('Unknown plot view at this journal position')
    for entity in page['entities']:
        required_kind={'context_size':'context','enabled':'context','generation':'generator','chat_activity':'turn'}.get(view)
        if required_kind and entity['kind']!=required_kind: continue
        state = entity['state']; label = f"{entity['kind']}:{entity['id']}"
        points = []; stride = 1
        if view == 'bytes':
            data = models.model_bytes(controller.journal.model(entity['model']))
            stride = max(1, math.ceil(len(data)/4096))
            points = [[i, data[i]] for i in range(0,len(data),stride)]
        elif view in ('context_size','revision','enabled','generation','chat_activity'):
            key = {'revision':'revision','enabled':'enabled','generation':'processed','chat_activity':'number'}.get(view)
            value = len(state.get('body','').encode('utf-8')) if view=='context_size' else state.get(key)
            if isinstance(value,(int,float)): points = [[entity['sequence'],int(value) if isinstance(value,bool) else value]]
        elif view in ('retrieval','candidates'):
            result = state.get('result', state)
            values = result.get('contexts',[]) if view=='retrieval' else result.get('candidates',[])
            points = [[i, v.get('retrieval_score',0) if view=='retrieval' else v.get('score',{}).get('value',0)] for i,v in enumerate(values)]
        if points: series.append({'label':label,'points':points,'display_stride':stride,'model':entity['model'],'kind':'step' if view=='bytes' else 'points'})
    if view in ('latency','outcomes'):
        values = []; cursor = 0
        while True:
            events = controller.events(selection,cursor,limit=1000,until=page['at_sequence'])
            for event in events['events']:
                payload = event.get('outcome') or {}
                value = payload.get('duration_seconds') if view=='latency' else {'completed':1,'committed':1,'failed':-1,'cancelled':-2,'indeterminate':-3}.get(event['phase'],0)
                if isinstance(value,(int,float)): values.append([event['seq'],value])
            if events['next_sequence'] is None: break
            cursor=events['next_sequence']
        series=[{'label':view,'points':values,'kind':'points'}]
    if view.startswith('view:'):
        definition=controller._inspect('view:'+view[5:],page['at_sequence'])['entities'][0]['state']
        series=definition['series']
    return {'schema':'controller-plot/1','at_sequence':page['at_sequence'],'view':view,
            'definition':next(v for v in definitions if v['id']==view),'series':series,
            'next_offset':page['next_offset'],'total':page['total'],
            'notice':'Plots are numerical displays. Recover state from the complete byte-function model, not from pixels.'}


def render_frame(plot, title, size=(1000,700)):
    from PIL import Image, ImageDraw, ImageFont
    image=Image.new('RGB',size,'#f8fafc'); draw=ImageDraw.Draw(image)
    font=ImageFont.load_default(size=16); small=ImageFont.load_default(size=12)
    width,height=size; left,top,right,bottom=70,90,width-30,height-120
    draw.text((25,20),title,fill='#0f172a',font=font)
    draw.text((25,48),f"{plot['view']} | sequence {plot['at_sequence']} | {plot['definition']['units']}",fill='#475569',font=small)
    points=[p for s in plot['series'] for p in s['points']]
    xmin=min((p[0] for p in points),default=0); xmax=max((p[0] for p in points),default=1)
    ymin=min((p[1] for p in points),default=0); ymax=max((p[1] for p in points),default=1)
    if xmax==xmin: xmax+=1
    if ymax==ymin: ymax+=1
    def xy(p): return (left+(p[0]-xmin)/(xmax-xmin)*(right-left),bottom-(p[1]-ymin)/(ymax-ymin)*(bottom-top))
    for i in range(6):
        x=left+(right-left)*i/5; y=top+(bottom-top)*i/5
        draw.line((x,top,x,bottom),fill='#cbd5e1'); draw.line((left,y,right,y),fill='#cbd5e1')
        draw.text((x-8,bottom+8),f'{xmin+(xmax-xmin)*i/5:.3g}',fill='#64748b',font=small)
        draw.text((5,y-6),f'{ymax-(ymax-ymin)*i/5:.3g}',fill='#64748b',font=small)
    colors=['#2563eb','#e11d48','#059669','#9333ea','#d97706']
    for index,series in enumerate(plot['series']):
        coordinates=[xy(p) for p in series['points']]; color=colors[index%len(colors)]
        if series.get('kind')=='step' and coordinates:
            coordinates=[coordinates[0]]+[p for a,b in zip(coordinates,coordinates[1:]) for p in ((b[0],a[1]),b)]
        if len(coordinates)>1: draw.line(coordinates,fill=color,width=2)
        elif coordinates:
            x,y=coordinates[0]; draw.ellipse((x-4,y-4,x+4,y+4),fill=color)
        if index<4: draw.text((25,bottom+40+index*16),series['label'][:110],fill=color,font=small)
    if len(plot['series'])>4: draw.text((25,height-18),f"+ {len(plot['series'])-4} additional series; see bundle for complete selection",fill='#475569',font=small)
    return image


def export_replay(controller, selection, start, end, format, *, view='bytes', stride=1,
                  include_lifecycle=False, fps=5, max_frames=500):
    start=controller._at(start)
    if start>end: raise ValueError('Replay range is reversed')
    if format not in ('gif','bundle'): raise ValueError('Export format must be gif or bundle')
    if type(stride) is not int or stride<1 or type(fps) is not int or not 1<=fps<=50: raise ValueError('Invalid frame stride or rate')
    if type(max_frames) is not int or not 1<=max_frames<=500: raise ValueError('Frame limit must be 1–500')
    selected_events=[]; sequences=[start]
    cursor=start-1
    while True:
        page=controller.events(selection,cursor,limit=1000,until=end)
        selected_events.extend(page['events'])
        if page['next_sequence'] is None: break
        cursor=page['next_sequence']
    for event in selected_events:
        if event['seq']!=start and (event['changes'] and event['phase'] in ('committed','baseline') or include_lifecycle): sequences.append(event['seq'])
    sequences=sequences[::stride]
    if len(sequences)>max_frames:
        raise ValueError(f'{len(sequences)} frames exceed {max_frames}; choose an explicit larger stride')
    exports=controller.directory/'exports'; exports.mkdir(exist_ok=True)
    identifier=str(uuid.uuid4()); bundle_path=exports/(identifier+'.zip')
    files={}; refs=set()
    frames=[]
    for sequence in sequences:
        entities=all_entities(controller,selection,sequence)
        refs.update(v['model'] for v in entities)
        # All selected entities remain in machine data, regardless of plot pagination.
        plot=plot_state(controller,selection,sequence,view,limit=1000)
        while plot.get('next_offset') is not None:
            next_page=plot_state(controller,selection,sequence,view,offset=plot['next_offset'],limit=1000)
            plot['series'].extend(next_page['series']); plot['next_offset']=next_page['next_offset']
        event=next(controller.journal.events(sequence-1,sequence))
        frame={'sequence':sequence,'timestamp':event['timestamp'],'entities':entities,'plot':plot}
        files[f'frames/{sequence}.json']=models.dumps(frame).encode()
        frames.append(frame)
    for event in selected_events:
        refs.add(event['payload_ref']); refs.update(c['model'] for c in event['changes'])
    files['events.json']=models.dumps(selected_events).encode()
    for ref in refs: files[f'models/{ref}.json']=json.dumps(controller.journal.model(ref),ensure_ascii=False).encode()
    manifest={'schema':'controller-replay/1','selection':selection,'start':start,'end':end,'sequences':sequences,
              'fps':fps,'stride':stride,'view':view,'files':{name:models.sha(value) for name,value in files.items()}}
    if format=='gif':
        images=[render_frame(f['plot'],f"Controller replay | frame {i+1}/{len(frames)}") for i,f in enumerate(frames)]
        output=io.BytesIO()
        images[0].save(output,format='GIF',save_all=True,append_images=images[1:],duration=1000//fps,loop=0,optimize=False,disposal=2)
        gif=output.getvalue(); files['replay.gif']=gif; manifest['files']['replay.gif']=models.sha(gif)
        from PIL import Image
        with Image.open(io.BytesIO(gif)) as decoded:
            manifest['gif_frames']=[]
            for index in range(decoded.n_frames):
                decoded.seek(index)
                manifest['gif_frames'].append({'sequence':sequences[index],'duration_ms':decoded.info['duration'],'rgb_sha256':models.sha(decoded.convert('RGB').tobytes())})
        (exports/(identifier+'.gif')).write_bytes(gif)
    files['manifest.json']=json.dumps(manifest,ensure_ascii=False,indent=2).encode()
    with zipfile.ZipFile(bundle_path,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,value in files.items(): archive.writestr(name,value)
    result={'bundle':str(bundle_path),'bundle_name':bundle_path.name,'frames':len(sequences),'sequences':sequences,'fps':fps}
    if format=='gif': result.update(gif=str(exports/(identifier+'.gif')),gif_name=identifier+'.gif')
    controller.emit('controller.export',result,phase='completed')
    return result


def verify_bundle(path):
    with zipfile.ZipFile(path) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('schema')!='controller-replay/1': raise ValueError('Unknown replay bundle')
        for name,digest in manifest['files'].items():
            data=archive.read(name)
            if models.sha(data)!=digest: raise ValueError('Replay bundle integrity failure')
            if name.startswith('models/'): models.decode(json.loads(data))
            if name.startswith('frames/'):
                frame=models.loads(data.decode())
                for entity in frame['entities']:
                    model=json.loads(archive.read(f"models/{entity['model']}.json"))
                    if models.dumps(models.decode(model))!=models.dumps(entity['state']): raise ValueError('Frame state does not match its recoverable model')
        if 'gif_frames' in manifest:
            from PIL import Image
            with Image.open(io.BytesIO(archive.read('replay.gif'))) as gif:
                if gif.n_frames!=len(manifest['sequences']): raise ValueError('GIF frame count mismatch')
                for i,expected in enumerate(manifest['gif_frames']):
                    gif.seek(i)
                    if expected['sequence']!=manifest['sequences'][i] or gif.info['duration']!=expected['duration_ms'] or models.sha(gif.convert('RGB').tobytes())!=expected['rgb_sha256']: raise ValueError('GIF frame integrity mismatch')
        return manifest


def branch(controller,sequence,destination):
    destination=Path(destination).resolve()
    if destination.exists(): raise ValueError('Branch destination must not already exist')
    values=all_entities(controller,None,sequence)
    destination.mkdir(parents=True)
    database=destination/'state'/'contexts.sqlite3'; database.parent.mkdir()
    from ..context_store import initialize, index_context
    with closing(sqlite3.connect(database)) as db, db:
        db.row_factory=sqlite3.Row; initialize(db)
        db.executescript('''CREATE TABLE chat_sessions (id TEXT PRIMARY KEY,title TEXT NOT NULL,revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE chat_turns (session_id TEXT,number INTEGER,request_id TEXT,prompt TEXT,topic TEXT,result TEXT,created_at TEXT,PRIMARY KEY(session_id,number),UNIQUE(session_id,request_id));''')
        contexts=[]
        for entity in values:
            state=dict(entity['state']); kind=entity['kind']
            if kind=='context':
                state['enabled']=int(state['enabled']); state['provenance']=json.dumps(state['provenance'],ensure_ascii=False)
                columns=('id','title','body','enabled','tags','provenance','generation_key','source_path','revision','created_at','updated_at')
                db.execute('INSERT INTO managed_contexts VALUES ('+','.join('?' for _ in columns)+')',[state.get(k) for k in columns]); contexts.append(state)
            elif kind=='chat': db.execute('INSERT INTO chat_sessions VALUES (?,?,?,?,?)',[state[k] for k in ('id','title','revision','created_at','updated_at')])
            elif kind=='turn':
                state['result']=json.dumps(state['result'],ensure_ascii=False)
                db.execute('INSERT INTO chat_turns VALUES (?,?,?,?,?,?,?)',[state[k] for k in ('session_id','number','request_id','prompt','topic','result','created_at')])
        snapshot=next((e for e in values if e['kind']=='index'),None)
        chunks={c['rowid']:c for c in snapshot['state']['chunks']} if snapshot else {}
        for entity in sorted((e for e in values if e['kind']=='index_part'),key=lambda e:e['sequence']):
            if snapshot and entity['sequence']<=snapshot['sequence']: continue
            context=next((c for c in contexts if c['id']==entity['id']),{})
            chunks={k:c for k,c in chunks.items() if not c['source'].startswith('context:'+entity['id']+' / ') and c['source']!=context.get('source_path')}
            chunks.update({c['rowid']:c for c in entity['state']['chunks']})
        for identifier,chunk in sorted(chunks.items()):
            db.execute('INSERT INTO chunks(rowid,source,text) VALUES (?,?,?)',(identifier,chunk['source'],chunk['text']))
            if chunk['source'].startswith('context:'):
                context_id=chunk['source'][8:].split(' / ')[0]
                db.execute('INSERT INTO managed_chunks VALUES (?,?)',(identifier,context_id))
    from .runtime import Controller
    config=dict(controller.config,index_path=str(database),context_dir=str(destination/'contexts'))
    Path(config['context_dir']).mkdir()
    child=Controller(config)
    child.journal.set_setting('parent',{'journal':str(controller.journal.path),'sequence':sequence})
    child.journal.set_setting('autonomy_paused',True)
    extras=[]
    for entity in values:
        if entity['kind']=='pandr':
            state=entity['state']
            path=child.directory/'pandr'/'controller.json'; path.parent.mkdir()
            # Copy immutable artifact bytes by hash; mutable source session state is never reused.
            artifacts=list(state.get('artifacts',{}).values())
            descriptors=artifacts+[d for a in artifacts for d in a.get('dependencies',[])]
            for artifact in descriptors:
                source=(controller.directory/'pandr'/artifact['path']).resolve(); target=(path.parent/artifact['path']).resolve()
                if not source.is_relative_to(controller.directory/'pandr') or not target.is_relative_to(path.parent): raise ValueError('Invalid historical artifact path')
                if not source.is_file(): raise ValueError('Historical P&R artifact bytes are unavailable')
                data=source.read_bytes()
                expected=artifact.get('sha256') or artifact.get('digest')
                if expected and models.sha(data)!=expected: raise ValueError('Historical artifact hash mismatch')
                target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(data)
            path.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8')
            child.emit('pandr.branch',phase='baseline',changes=[entity | {'id':'main'}])
        elif entity['kind']=='skill':
            item=dict(entity['state'],enabled=False,grant=None,approved_digest=None)
            with child.journal.connect() as db: db.execute('INSERT INTO skills VALUES (?,?)',(item['id'],models.dumps(item)))
            extras.append({'kind':'skill','id':item['id'],'state':item})
        elif entity['kind']=='proposal':
            item=dict(entity['state'])
            if item['status'] in ('pending','executing','indeterminate'):
                item.update(parent_status=item['status'],status='inactive_branch')
            with child.journal.connect() as db: db.execute('INSERT INTO proposals VALUES (?,?)',(item['id'],models.dumps(item)))
            extras.append({'kind':'proposal','id':item['id'],'state':item})
        elif entity['kind'] not in ('context','chat','turn','index'):
            extras.append(entity)
            if entity['kind']=='filter': child.journal.set_setting('filter:'+entity['id'],entity['state'])
    if extras: child.emit('controller.branch_state',phase='baseline',changes=extras)
    child.emit('controller.branched',{'parent_sequence':sequence},phase='completed')
    (destination/'configs').mkdir()
    (destination/'configs'/'controller.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    child.close()
    controller.emit('controller.branch',{'sequence':sequence,'destination':str(destination)},phase='completed')
    return {'destination':str(destination),'database':str(database),'parent_sequence':sequence,'autonomy_paused':True}
