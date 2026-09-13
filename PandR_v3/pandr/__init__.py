"""Point & Resolve: fixed points, explicit directions, deterministic contracts."""
__version__='2.0.0'

def __getattr__(name):
    if name in ('Curve', 'Plot', 'plot'):
        from . import plotting
        return getattr(plotting, name)
    if name=='Runtime':
        from .runtime import Runtime
        return Runtime
    if name=='Function':
        from .expressions import Function
        return Function
    raise AttributeError(name)
