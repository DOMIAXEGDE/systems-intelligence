"""P&R controller shared by all systems-intelligence interfaces."""
from pathlib import Path
import threading
import weakref

_instances = weakref.WeakValueDictionary()
_lock = threading.RLock()


def get_controller(config):
    from .runtime import Controller
    key = str(Path(config['index_path']).resolve())
    with _lock:
        controller = _instances.get(key)
        if controller is None or controller.closed:
            controller = Controller(config)
            _instances[key] = controller
        else:
            controller.config.update(config)
        return controller


def __getattr__(name):
    if name == 'Controller':
        from .runtime import Controller
        return Controller
    raise AttributeError(name)
