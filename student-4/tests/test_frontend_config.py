"""Frontend configuration and static-asset wiring.

These exist because a path bug that only appears inside the container is
invisible to every other test: the app runs fine from a checkout and dies
on startup once WORKDIR is /app. Both layouts are exercised here.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


def load_frontend(monkeypatch, base_dir=None, env=None):
    """Import the frontend module fresh, optionally faking where it lives."""
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    for key in ("SHARED_FRONTEND_DIR",):
        if not (env or {}).get(key):
            monkeypatch.delenv(key, raising=False)

    sys.modules.pop("rb_frontend_app", None)
    spec = importlib.util.spec_from_file_location("rb_frontend_app", FRONTEND_DIR / "app.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["rb_frontend_app"] = module
    spec.loader.exec_module(module)

    if base_dir is not None:
        monkeypatch.setattr(module, "BASE_DIR", Path(base_dir))
    return module


def test_shared_dir_defaults_to_the_repository_theme(monkeypatch):
    module = load_frontend(monkeypatch)
    assert module.SHARED_FRONTEND_DIR.name == "frontend"
    assert module.SHARED_FRONTEND_DIR.parent.name == "shared"
    assert (module.SHARED_FRONTEND_DIR / "css" / "main.css").exists()


def test_environment_variable_overrides_the_default(monkeypatch):
    module = load_frontend(monkeypatch, env={"SHARED_FRONTEND_DIR": "/app/shared"})
    assert module.SHARED_FRONTEND_DIR == Path("/app/shared")


@pytest.mark.parametrize("base", ["/app", "/", "/srv/x/y/frontend"])
def test_default_never_raises_for_a_shallow_path(monkeypatch, base):
    """WORKDIR /app leaves BASE_DIR with a single parent.

    Indexing parents[1] blindly raises IndexError there, which crashed the
    container on import before the environment variable could be read.
    """
    module = load_frontend(monkeypatch, base_dir=base)
    resolved = module._default_shared_dir()
    assert resolved.name == "frontend"
    assert resolved.parent.name == "shared"


def test_shared_theme_is_served_over_http(monkeypatch):
    """The page links /shared/css/main.css, so the route must answer."""
    module = load_frontend(monkeypatch)
    client = module.app.test_client()

    for asset in ("main.css", "variables.css", "reset.css", "layout.css", "components.css"):
        response = client.get("/shared/css/" + asset)
        assert response.status_code == 200, asset
        assert response.data, asset


def test_feature_css_builds_on_shared_tokens(monkeypatch):
    """Feature CSS must read shared tokens, not hard-code its own colours."""
    css = (FRONTEND_DIR / "static" / "css" / "feature.css").read_text(encoding="utf-8")
    assert "var(--color-" in css

    declarations = [
        line for line in css.splitlines()
        if ":" in line and not line.strip().startswith(("/*", "*", "//"))
    ]
    hard_coded = [
        line.strip() for line in declarations
        if "#" in line.split(":", 1)[1] and "var(--" not in line
    ]
    assert not hard_coded, "hard-coded colours found: " + "; ".join(hard_coded[:3])


def test_htmx_is_vendored_not_loaded_from_a_cdn():
    """The showcase may have no internet."""
    base = (FRONTEND_DIR / "templates" / "base.html").read_text(encoding="utf-8")
    assert "unpkg.com" not in base
    assert "cdn.jsdelivr" not in base
    assert "js/htmx.min.js" in base
    assert (FRONTEND_DIR / "static" / "js" / "htmx.min.js").exists()


def test_booking_form_offers_every_bed_not_only_free_ones(monkeypatch):
    """The dropdown must not pre-filter to available beds.

    Hiding busy beds hides the conflict rule: a coordinator could never
    attempt a clashing booking, so the API's 409 would be unreachable from
    the interface. The form offers every bed and lets the API refuse.
    """
    module = load_frontend(monkeypatch)
    calls = []

    def fake_api(method, path, **kwargs):
        calls.append((method, path, kwargs.get("params")))
        return [], None

    monkeypatch.setattr(module, "api", fake_api)
    client = module.app.test_client()
    assert client.get("/arrangements").status_code == 200

    availability = [c for c in calls if c[1] == "/api/rooms/availability"]
    assert availability, "the page never asked for bed availability"
    for _, _, params in availability:
        assert not (params or {}).get("bed_status"), \
            "availability was filtered, so busy beds are missing from the form"
