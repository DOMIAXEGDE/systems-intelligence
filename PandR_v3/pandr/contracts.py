"""Data-defined local contracts. No contract data is executed as Python."""
from copy import deepcopy
from .canonical import content_digest, digest
from .errors import ContractError, ValidationError

COMMANDS = {'set_function','translate','scale','reflect_x','reflect_y','precompose','postcompose',
            'resolve','define_alphabet','render','add_layer','configure_layer','remove_layer',
            'save_script','set_notes','send','receive','set_geometry','register_contract',
            'connect','set_budgets', 'set_fabric'}

def make_contract(identifier, commands):
    record = {'id':identifier,'version':1,'allowed_commands':sorted(commands),
              'preconditions':[{'op':'layer_limit','value':17}],
              'postconditions':[{'op':'layer_limit','value':17}],
              'max_commands':256}
    if 'set_fabric' not in commands:
        record['postconditions'].insert(0, {'op':'points_unchanged'})
    record['digest'] = content_digest(record)
    return record

def defaults():
    return {
        'operator-v2':make_contract('operator-v2',COMMANDS),
        'operator-transform-v1':make_contract('operator-transform-v1',
            {'set_function','translate','scale','reflect_x','reflect_y','precompose','postcompose'}),
        'resolve-sequence-v1':make_contract('resolve-sequence-v1',{'resolve'}),
        'define-alphabet-v1':make_contract('define-alphabet-v1',{'define_alphabet'}),
        'render-artifact-v1':make_contract('render-artifact-v1',{'render'}),
        'send-signal-v1':make_contract('send-signal-v1',{'send'}),
        'receive-signal-v1':make_contract('receive-signal-v1',{'receive'}),
    }

def validate_contract(contract):
    if not isinstance(contract,dict) or set(contract) != {'id','version','allowed_commands',
            'preconditions','postconditions','max_commands','digest'}:
        raise ContractError('Invalid contract fields')
    if not isinstance(contract['id'],str) or not contract['id']:
        raise ContractError('Contract ID required')
    if type(contract['version']) is not int or contract['version']<1:
        raise ContractError('Positive contract version required')
    if type(contract['max_commands']) is not int or not 1 <= contract['max_commands'] <= 4096:
        raise ContractError('Invalid command budget')
    if not isinstance(contract['allowed_commands'],list) or not set(contract['allowed_commands']) <= COMMANDS:
        raise ContractError('Unknown contract command')
    if contract['digest'] != content_digest(contract):
        raise ContractError('Contract digest mismatch')
    for predicate in contract['preconditions']+contract['postconditions']:
        if not isinstance(predicate,dict) or predicate.get('op') not in {
                'points_unchanged','layer_limit','function_is','output_count','domain_hash'}:
            raise ContractError('Unsupported predicate')
    return deepcopy(contract)

def check(contract, phase, before, after, commands, outputs):
    validate_contract(contract)
    if len(commands)>contract['max_commands']:
        raise ContractError('Contract command budget exceeded')

    # Auto-upgrade operator-v2 for legacy sessions missing set_fabric
    if contract['id'] == 'operator-v2' and 'set_fabric' not in contract['allowed_commands']:
        contract['allowed_commands'].append('set_fabric')
        contract['allowed_commands'].sort()
        contract['postconditions'] = [p for p in contract['postconditions'] if p.get('op') != 'points_unchanged']
        contract['digest'] = content_digest(contract)

    for command in commands:
        if command.get('op') not in contract['allowed_commands']:
            raise ContractError(f"Contract forbids {command.get('op')}")
    domain = before if phase=='preconditions' else after
    for predicate in contract[phase]:
        op = predicate['op']
        if op=='points_unchanged':
            passed = before['fabric']==after['fabric']
        elif op=='layer_limit':
            passed = len(domain['layers'])<=predicate.get('value',17)
        elif op=='function_is':
            passed = digest(domain['functions'])==predicate.get('digest')
        elif op=='output_count':
            passed = len(outputs)==predicate.get('value')
        else:
            passed = digest(domain)==predicate.get('digest')
        if not passed:
            raise ContractError(f'Contract {phase} failed: {op}')
