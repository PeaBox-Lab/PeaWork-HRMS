from __future__ import annotations

import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
TEST_DB = DATA_DIR / "test_v2.db"
TEST_STORAGE = DATA_DIR / "test_v2_documents"

DATA_DIR.mkdir(parents=True, exist_ok=True)
shutil.rmtree(TEST_STORAGE, ignore_errors=True)
for suffix in ("", "-wal", "-shm"):
    Path(str(TEST_DB) + suffix).unlink(missing_ok=True)

# Environment is applied per test rather than at import time: the legacy v1
# suite also reads ``PEAWORK_*`` variables, so mutating the process environment
# during collection would leak the v2 test database into v1 tests.
TEST_ENV = {
    "PEAWORK_ENV": "test",
    "PEAWORK_DEBUG": "false",
    "PEAWORK_SECRET_KEY": "test-secret-key-that-is-long-enough-1234567890",
    "PEAWORK_DATABASE_URL": f"sqlite:///{TEST_DB.as_posix()}",
    "PEAWORK_STORAGE_DIR": str(TEST_STORAGE),
    "PEAWORK_BOOTSTRAP_ADMIN_EMAIL": "admin@test.local",
    "PEAWORK_BOOTSTRAP_ADMIN_NAME": "Test Administrator",
    "PEAWORK_BOOTSTRAP_ADMIN_PASSWORD": "AdminTest123!",
    "PEAWORK_ORGANIZATION_NAME": "PeaWork Test Organisation",
    "PEAWORK_COOKIE_SECURE": "false",
    "PEAWORK_SEED_DEMO_DATA": "true",
    "PEAWORK_LOG_LEVEL": "WARNING",
    "PEAWORK_BASE_URL": "http://testserver",
}

from fastapi.testclient import TestClient  # noqa: E402

from peawork.config import get_settings, reset_settings_cache  # noqa: E402
from peawork.db import reset_engine, session_scope  # noqa: E402
from peawork.main import create_app  # noqa: E402
from peawork.models import Base  # noqa: E402
from peawork.services.bootstrap import bootstrap  # noqa: E402
from peawork.db import create_all, get_engine  # noqa: E402

ADMIN_EMAIL = "admin@test.local"
ADMIN_PASSWORD = "AdminTest123!"
MANAGER_EMAIL = "manager@example.com"
MANAGER_PASSWORD = "ManagerDemo123!"
EMPLOYEE_EMAIL = "employee@example.com"
EMPLOYEE_PASSWORD = "EmployeeDemo123!"


@pytest.fixture(autouse=True)
def v2_environment(monkeypatch):
    """Apply v2 settings for the duration of one test and restore v1 vars."""
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    reset_settings_cache()
    reset_engine()
    yield
    reset_engine()
    reset_settings_cache()


@pytest.fixture()
def client():
    reset_engine()
    engine = get_engine()
    Base.metadata.drop_all(bind=engine)
    create_all()
    with session_scope() as db:
        bootstrap(db, get_settings(), with_demo=True)

    application = create_app()
    with TestClient(application, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def second_client(client):
    """An independent cookie jar for multi-user scenarios.

    Depends on ``client`` so the database is created and bootstrapped even when
    a test only needs a second browser session.
    """
    application = create_app()
    with TestClient(application, follow_redirects=False) as test_client:
        yield test_client


@pytest.fixture()
def third_client(client):
    """A third independent cookie jar for three-role scenarios."""
    application = create_app()
    with TestClient(application, follow_redirects=False) as test_client:
        yield test_client
