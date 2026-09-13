import os
from pandr import Runtime
from pandr.alphabets import create_alphabet

with Runtime.open(os.environ['PANDR_SESSION']) as pr:
    # Programmatically sets original pixels, with no font rasterization.
    manifest=create_alphabet('operator-pixels',count=95,width=12,height=12)
    print(pr.commit(pr.plan().define_alphabet(manifest)))
