import os
from pandr import Runtime, Function

with Runtime.open(os.environ['PANDR_SESSION']) as pr:
    plan=pr.plan().set_function(Function.parse('x+3'))
    plan.transform.postcompose(Function.parse('2*x'))
    print(pr.commit(plan))  # 2*x+6, explicitly g(f(x))
