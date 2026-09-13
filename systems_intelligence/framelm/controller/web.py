"""Controller HTTP operations; called behind Studio's existing host/CSRF checks."""
import uuid


def get(controller, operation, query):
    import json
    def value(name, default=None): return query.get(name,[default])[0]
    selection=json.loads(value('selection','null'))
    sequence=int(value('at')) if value('at') is not None else None
    if operation=='registry': return controller.registry()
    if operation=='inspect':
        return controller.inspect(selection,sequence,offset=int(value('offset','0')),limit=int(value('limit','100')),include_models=value('models')=='1')
    if operation=='selection':
        from .visuals import all_entities
        page=controller.inspect(selection,sequence,limit=1)
        page['entities']=all_entities(controller,selection,page['at_sequence']); page['next_offset']=None
        if value('models')=='1':
            for entity in page['entities']: entity['function_model']=controller.journal.model(entity['model'])
        return page
    if operation=='events': return controller.events(selection,int(value('after','0')),int(value('limit','100')),sequence)
    if operation=='plot': return controller.plot(selection,sequence,value('view','bytes'),int(value('offset','0')),int(value('limit','100')))
    if operation=='skills': return controller.skills.list()
    if operation=='filters':
        from .models import loads
        with controller.journal.connect() as db:
            return {'filters':{r['key'][7:]:loads(r['value']) for r in db.execute("SELECT * FROM settings WHERE key LIKE 'filter:%'")}}
    raise ValueError('Unknown controller query')


def post(controller, operation, data):
    if operation=='dispatch': return controller.dispatch(data['command'],data.get('payload',{}),data.get('request_id') or str(uuid.uuid4()),data.get('expected_revision'))
    commands={'export':'controller.export','branch':'controller.branch','filter':'controller.filter.save',
              'skills/draft':'skill.draft','skills/activate':'skill.activate','skills/disable':'skill.disable',
              'skills/grant':'skill.grant','skills/revoke':'skill.revoke','skills/run':'skill.run',
              'skills/cancel':'skill.cancel','skills/pause':'skill.pause','proposals/approve':'proposal.approve',
              'proposals/reject':'proposal.reject'}
    if operation in commands: return controller.dispatch(commands[operation],data,str(uuid.uuid4()))
    raise ValueError('Unknown controller operation')
