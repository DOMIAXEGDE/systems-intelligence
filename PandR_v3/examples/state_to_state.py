import os
from pathlib import Path
from pandr import Runtime

source_path=Path(os.environ['PANDR_SESSION'])
destination=source_path.with_name('operator-consumer.json')
consumer=Runtime.open(destination) if destination.exists() else Runtime.create(destination)
with Runtime.open(source_path) as pr:
    pr.commit(pr.plan().set_function('x').resolve('v17 v7 v3 v23 h37 v23').send(destination))
    print(pr.drain_outbox())
    consumer.reload()
    print(consumer.snapshot()['domain']['last_ranks'])
