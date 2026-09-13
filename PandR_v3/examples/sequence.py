import os
from pandr import Runtime

with Runtime.open(os.environ['PANDR_SESSION']) as pr:
    plan=pr.plan().set_function('x')
    plan.resolve('v17 + v7 + v3 + v23 + h-distance37 + v23')
    print(pr.commit(plan))
    print(pr.resolve('v-down17 + h-up37'))
