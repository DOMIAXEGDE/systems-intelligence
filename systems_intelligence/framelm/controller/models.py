"""Recoverable state models. Pixel renders are never treated as source data."""
import hashlib
import json
import math
from pandr.canonical import canonical
from pandr.codecs import rank_bytes, unrank_bytes


def typed(value):
    if value is None: return ['null']
    if type(value) is bool: return ['bool', value]
    if type(value) is int: return ['int', str(value)]
    if type(value) is float:
        if not math.isfinite(value): raise ValueError('State numbers must be finite')
        return ['float64', value.hex()]
    if isinstance(value, str): return ['text', value]
    if isinstance(value, bytes): return ['bytes', value.hex()]
    if isinstance(value, (list, tuple)):
        return ['tuple' if isinstance(value, tuple) else 'list', [typed(v) for v in value]]
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return ['map', [[k, typed(v)] for k, v in value.items()]]
    raise ValueError(f'Unsupported state type: {type(value).__name__}')


def untyped(value):
    kind = value[0]
    if kind == 'null': return None
    if kind in ('bool', 'text'): return value[1]
    if kind == 'int': return int(value[1])
    if kind == 'float64': return float.fromhex(value[1])
    if kind == 'bytes': return bytes.fromhex(value[1])
    if kind in ('list', 'tuple'):
        data = [untyped(v) for v in value[1]]
        return tuple(data) if kind == 'tuple' else data
    if kind == 'map': return {k: untyped(v) for k, v in value[1]}
    raise ValueError('Unknown typed state value')


def dumps(value): return canonical(typed(value)).decode('utf-8')
def loads(value): return untyped(json.loads(value))
def sha(data): return hashlib.sha256(data).hexdigest()


def encode(value):
    data = dumps(value).encode('utf-8')
    blocks = []
    for start in range(0, len(data), 256):
        part = data[start:start+256]
        blocks.append({'offset': start, 'length': len(part), 'sha256': sha(part), 'rank': rank_bytes(part)})
    return {'schema': 'controller-byte-function/1', 'encoding': 'typed-canonical-json/1',
            'function': 'f(i)=byte[i] on [i,i+1)', 'length': len(data), 'sha256': sha(data), 'blocks': blocks}


def model_bytes(model):
    if model.get('schema') != 'controller-byte-function/1': raise ValueError('Unsupported state model')
    data = bytearray()
    for block in model['blocks']:
        part = unrank_bytes(block['rank'], max_length=256)
        if block['offset'] != len(data) or block['length'] != len(part) or sha(part) != block['sha256']:
            raise ValueError('State block integrity failure')
        data.extend(part)
    if len(data) != model['length'] or sha(data) != model['sha256']: raise ValueError('State model integrity failure')
    return bytes(data)


def decode(model): return loads(model_bytes(model))
