"""Test fixtures for the Room & Bed Management microservices.

The backend normally reaches its database over HTTP on port 6400. For
tests, `_call` in database_client is redirected into the database
service's own Flask test client, so the two services are exercised
together with no network and no ports. Each test gets a freshly built
database in a temporary directory, so tests never share state.

Both services have a file called app.py, so each is loaded under an
explicit module name rather than by plain `import app`.
"""

import importlib
import importlib.util
import shutil
import sys
from pathlib import Path

import pytest

STUDENT_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = STUDENT_DIR / "backend"
DATABASE_DIR = STUDENT_DIR / "database"

for path in (BACKEND_DIR, DATABASE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def load_module(name, path):
    """Import a file under an explicit module name, replacing any cached copy."""
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def database_app(tmp_path, monkeypatch):
    """A database service whose SQLite file lives in a temp directory."""
    work = tmp_path / "database"
    work.mkdir()
    for name in ("schema.sql", "seed_data.sql", "init_db.py", "db.py", "app.py"):
        shutil.copy(DATABASE_DIR / name, work / name)

    monkeypatch.setenv("DB_PATH", str(work / "rooms.db"))
    monkeypatch.syspath_prepend(str(work))

    db_module = load_module("db", work / "db.py")
    init_db = load_module("rb_init_db", work / "init_db.py")
    init_db.build(db_module.DB_PATH).close()

    service = load_module("rb_database_app", work / "app.py")
    service.app.config.update(TESTING=True)
    return service.app


@pytest.fixture()
def api(database_app, monkeypatch):
    """Backend test client wired to the temporary database service."""
    db_client = database_app.test_client()

    from responses import ApiError
    dbc = importlib.import_module("services.database_client")
    importlib.reload(dbc)

    def routed_call(method, path, params=None, json=None):
        response = db_client.open(path, method=method, query_string=params or {}, json=json)
        body = response.get_json()
        if not body or not body.get("success"):
            message = (body or {}).get("error", "database error")
            raise ApiError(message, status=response.status_code)
        return body["data"]

    monkeypatch.setattr(dbc, "_call", routed_call)

    backend = load_module("rb_backend_app", BACKEND_DIR / "app.py").create_app()
    backend.config.update(TESTING=True)
    return backend.test_client()


def data(response):
    """Unwrap the success envelope, failing loudly on an error response."""
    body = response.get_json()
    assert body["success"], body["error"]
    return body["data"]


def error(response):
    body = response.get_json()
    assert not body["success"], "expected a failure response"
    return body["error"]
