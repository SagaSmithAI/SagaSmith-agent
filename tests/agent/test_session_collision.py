"""Regression tests for collision-resistant session filenames."""

from pathlib import Path

from nanobot.session.manager import Session, SessionManager


def _manager(tmp_path: Path) -> SessionManager:
    return SessionManager(tmp_path / "workspace")


def test_distinct_keys_have_distinct_filenames(tmp_path: Path) -> None:
    sm = _manager(tmp_path)

    first = sm._get_session_path("telegram:a_b")
    second = sm._get_session_path("telegram:a:b")

    assert first.name != second.name
    assert sm.safe_key("telegram:a_b") == sm.safe_key("telegram:a:b")
    assert sm._storage_key("telegram:a_b") != sm._storage_key("telegram:a:b")


def test_save_uses_collision_resistant_path(tmp_path: Path) -> None:
    sm = _manager(tmp_path)
    key = "telegram:a:b"
    session = Session(key=key)
    session.add_message("user", "first")
    sm.save(session)

    new_path = sm._get_session_path(key)
    session.add_message("assistant", "latest content")
    sm.save(session)

    assert new_path.exists()
    assert "latest content" in new_path.read_text(encoding="utf-8")


def test_safe_key_is_lossy() -> None:
    assert SessionManager.safe_key("telegram:a_b") == SessionManager.safe_key("telegram:a:b")


def test_storage_key_is_collision_resistant() -> None:
    encoded = {
        SessionManager._storage_key("a:b"),
        SessionManager._storage_key("a_b"),
        SessionManager._storage_key("a:b:c"),
    }

    assert len(encoded) == 3
    assert SessionManager._storage_key("telegram:a_b") != SessionManager._storage_key(
        "telegram:a:b"
    )


def test_storage_paths_are_distinct_when_keys_collide_under_safe_key(
    tmp_path: Path,
) -> None:
    sm = _manager(tmp_path)
    first = Session(key="telegram:a_b")
    first.add_message("user", "underscore history")
    second = Session(key="telegram:a:b")
    second.add_message("user", "colon history")

    sm.save(first)
    sm.save(second)

    assert sm.safe_key(first.key) == sm.safe_key(second.key)
    assert sm._get_session_path(first.key).exists()
    assert sm._get_session_path(second.key).exists()
    assert sm._get_session_path(first.key) != sm._get_session_path(second.key)

    sm.invalidate(first.key)
    sm.invalidate(second.key)
    loaded_first = sm._load(first.key)
    loaded_second = sm._load(second.key)

    assert loaded_first is not None
    assert loaded_second is not None
    assert loaded_first.messages[0]["content"] == "underscore history"
    assert loaded_second.messages[0]["content"] == "colon history"
