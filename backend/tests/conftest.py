import pytest


@pytest.fixture(autouse=True)
def isolate_persistence(tmp_path, monkeypatch):
    """Tests must never write to the user's actual chat/memory database."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "nova-test.db"))
