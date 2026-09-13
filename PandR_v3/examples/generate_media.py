import os
from pandr import Runtime

with Runtime.open(os.environ['PANDR_SESSION']) as pr:
    plan=pr.plan().set_function('x').resolve('v17 v7 v3 v23 h37 v23')
    active={item['type'] for item in pr.snapshot()['domain']['layers'] if item['enabled']}
    for kind in ['text','image','audio','video','code']:
        if kind not in active: plan.add_layer(kind)
        plan.render(kind)
    print(pr.commit(plan))
