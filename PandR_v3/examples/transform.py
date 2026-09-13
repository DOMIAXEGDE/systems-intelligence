"""Run through python -m pandr run examples/transform.py --session PATH."""
import os
from pandr import Runtime

with Runtime.open(os.environ['PANDR_SESSION']) as pr:
    points_before=pr.snapshot()['domain']['fabric']
    plan=pr.plan().set_function('x')
    plan.transform.translate(dx='2',dy='5')
    print(pr.commit(plan)['after_domain_hash'])
    assert pr.snapshot()['domain']['fabric']==points_before
