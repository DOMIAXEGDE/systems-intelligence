"""Complete reproducible local demonstration; creates new sessions only."""
from pathlib import Path
from .runtime import Runtime
from .errors import ValidationError

SEQUENCE='v17 + v7 + v3 + v23 + h-distance37 + v23'

def create_demo(directory):
    directory=Path(directory).resolve()
    producer_path=directory/'producer.json'; consumer_path=directory/'consumer.json'
    if producer_path.exists() or consumer_path.exists():
        raise ValidationError('Demo output already contains sessions; choose a new output directory')
    producer=Runtime.create(producer_path); consumer=Runtime.create(consumer_path)
    plan=producer.plan().set_notes('P&R demonstration: fixed prime coordinates; four direction families; all five exports.')
    for kind in ('image','audio','video','code'): plan.add_layer(kind)
    plan.resolve(SEQUENCE)
    for kind in ('text','image','audio','video','code'): plan.render(kind)
    media=producer.commit(plan)
    # A transform and its composition visibly change the curve, never the points.
    plan=producer.plan(); plan.transform.translate(dx='2',dy='5'); plan.transform.postcompose('2*x')
    producer.commit(plan)
    four_directions={f'{contact}-{plane}':producer.measure(plane,contact) for contact in ('v','h') for plane in ('horizontal','vertical')}
    producer.commit(producer.plan().set_function('x').resolve('v-down17 + h-up37'))
    plan=consumer.plan().add_layer('image')
    plan.configure_layer('numeric',{'config':{'on_rank_stream':['text','image']}})
    consumer.commit(plan)
    producer.commit(producer.plan().resolve(SEQUENCE).send(consumer_path))
    delivery=producer.drain_outbox()
    consumer.reload()
    manifests=[a for a in consumer.snapshot()['artifacts'].values() if 'kind' in a]
    consumer.commit(consumer.plan().send(producer_path,[{'sha256':a['sha256'],'path':a['path']} for a in manifests],payload_type='artifact-reference'))
    returned=consumer.drain_outbox(); producer.reload()
    producer_check=producer.validate(); consumer_check=consumer.validate()
    replay=producer.replay(directory/'replay')
    bundle=producer.export_bundle(directory/'producer.pandr.zip')
    return {'status':'complete','producer':str(producer_path),'consumer':str(consumer_path),
            'outputs':[output['data'] for output in media['outputs'] if output['type']=='artifact'],
            'measurement_families':list(four_directions),'delivery':delivery,'return_delivery':returned,
            'producer_validation':producer_check,'consumer_validation':consumer_check,'replay':replay,'bundle':bundle}
