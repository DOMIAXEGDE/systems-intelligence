"""P&R canonical JSON, strict parsing and content identities."""
import hashlib
import json
from .errors import ValidationError

def canonical(value):
    def check(item):
        if item is None or type(item) in (bool, int):
            return
        if isinstance(item, str):
            try:
                item.encode('utf-8')
            except UnicodeEncodeError as e:
                raise ValidationError('Lone Unicode surrogate') from e
            return
        if isinstance(item, list):
            for child in item:
                check(child)
            return
        if isinstance(item, dict) and all(isinstance(k,str) for k in item):
            for k,v in item.items():
                check(k); check(v)
            return
        raise ValidationError(f'Noncanonical JSON value: {type(item).__name__}')
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def content_digest(value, field='digest'):
    return digest({k:v for k,v in value.items() if k != field})

def strict_json(text):
    def pairs(items):
        value = {}
        for key, child in items:
            if key in value:
                raise ValidationError(f'Duplicate JSON key: {key}')
            value[key] = child
        return value
    def reject(value):
        raise ValidationError(f'Nonfinite JSON number: {value}')
    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_constant=reject)
        canonical(value)
        return value
    except (ValueError, TypeError, UnicodeError) as e:
        raise ValidationError(f'Invalid JSON: {e}') from e
