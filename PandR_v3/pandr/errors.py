"""Stable, serializable application errors."""
class PandRError(RuntimeError):
    code = 'pandr_error'

class ValidationError(PandRError):
    code = 'validation_error'

class ResolutionError(PandRError):
    code = 'resolution_error'

class BudgetExceeded(ValidationError):
    code = 'budget_exceeded'

class RevisionConflict(PandRError):
    code = 'revision_conflict'

class RequestIdConflict(PandRError):
    code = 'request_id_conflict'

class UnsupportedSchema(ValidationError):
    code = 'unsupported_schema'

class ContractError(ValidationError):
    code = 'contract_error'
