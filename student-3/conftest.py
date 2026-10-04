"""Lets `pytest student-3` run all three service suites in one process.

The database, backend and frontend services each have their own top-level
`app` module (and the backend a `services` package). This keeps one set of
those modules per service and makes the right set current while that service's
tests are collected and run. Running each suite on its own needs none of this.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
SERVICES = ("database", "backend", "frontend")
_modules = {name: {} for name in SERVICES}
_current = None


def _service_of(path):
    try:
        first = Path(str(path)).resolve().relative_to(ROOT).parts[0]
    except (ValueError, IndexError):
        return None
    return first if first in SERVICES else None


def _is_local(module):
    origin = getattr(module, "__file__", None)
    if origin is None:  # namespace packages
        locations = list(getattr(module, "__path__", []))
        origin = locations[0] if locations else None
    return origin is not None and _service_of(origin) is not None and "tests" not in Path(origin).parts


def _activate(service):
    """Swap the service-local modules in sys.modules and put the service first on sys.path."""
    global _current
    if service is None or service == _current:
        return
    local = {name: module for name, module in sys.modules.items() if _is_local(module)}
    if _current is not None:
        _modules[_current].update(local)
    for name in local:
        del sys.modules[name]
    sys.modules.update(_modules[service])
    service_dirs = {str(ROOT / name) for name in SERVICES}
    sys.path[:] = [str(ROOT / service)] + [entry for entry in sys.path if entry not in service_dirs]
    _current = service


def pytest_collectstart(collector):
    _activate(_service_of(getattr(collector, "path", "")))


def pytest_runtest_setup(item):
    _activate(_service_of(item.path))
