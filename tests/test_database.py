from __future__ import annotations

from pathlib import Path

from codex_cil.database import Database
from codex_cil.models import SessionKind, UsageStats


def test_project_session_and_usage_round_trip(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")

    project = db.add_project("Demo", str(workspace))
    session = db.create_session(
        SessionKind.PROJECT,
        "First work",
        project_id=project.id,
        model="gpt-6.1-sol",
    )
    db.add_message(SessionKind.PROJECT, session.id, "user", "hello")
    usage = UsageStats(
        input_tokens=100,
        cached_input_tokens=80,
        output_tokens=20,
        total_tokens=120,
        context_tokens=65,
        context_window=200,
    )
    db.save_usage(SessionKind.PROJECT, session.id, usage)

    loaded_project = db.get_project(project.id)
    loaded_session = db.get_session(session.id, SessionKind.PROJECT)
    loaded_messages = db.list_messages(SessionKind.PROJECT, session.id)
    loaded_usage = db.load_usage(SessionKind.PROJECT, session.id)

    assert loaded_project.path == str(workspace.resolve())
    assert loaded_session.title == "First work"
    assert loaded_messages[0].content == "hello"
    assert loaded_usage.total_tokens == 120
    assert loaded_usage.uncached_input_tokens == 20
    assert loaded_usage.cache_hit_ratio == 0.8
    assert loaded_usage.context_ratio == 0.325
    db.close()


def test_pure_chat_is_separate_from_projects(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    project = db.add_project("Demo", str(workspace))
    db.create_session(
        SessionKind.PROJECT,
        "Project chat",
        project_id=project.id,
    )
    pure = db.create_session(SessionKind.PURE_CHAT, "Pure chat")

    assert len(db.list_sessions(SessionKind.PROJECT, project.id)) == 1
    assert len(db.list_sessions(SessionKind.PURE_CHAT)) == 1
    assert db.get_session(pure.id, SessionKind.PURE_CHAT).project_id is None
    db.close()


def test_delete_project_cascades_project_sessions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    project = db.add_project("Demo", str(workspace))
    session = db.create_session(
        SessionKind.PROJECT,
        "Project chat",
        project_id=project.id,
    )
    pure = db.create_session(SessionKind.PURE_CHAT, "Pure chat")

    db.delete_project(project.id)

    assert db.list_projects() == []
    assert db.list_sessions(SessionKind.PROJECT, project.id) == []
    with_raises = False
    try:
        db.get_session(session.id, SessionKind.PROJECT)
    except KeyError:
        with_raises = True
    assert with_raises
    assert db.get_session(pure.id, SessionKind.PURE_CHAT).title == "Pure chat"
    db.close()


def test_legacy_placeholder_titles_become_jarvis(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    db = Database(tmp_path / "app.db")
    project = db.add_project("Demo", str(workspace))
    project_session = db.create_session(
        SessionKind.PROJECT,
        "Demo 新对话",
        project_id=project.id,
    )
    pure_session = db.create_session(SessionKind.PURE_CHAT, "新纯对话")

    db.normalize_default_session_titles()

    assert (
        db.get_session(project_session.id, SessionKind.PROJECT).title
        == "贾维斯"
    )
    assert (
        db.get_session(pure_session.id, SessionKind.PURE_CHAT).title
        == "贾维斯"
    )
    db.close()


def test_message_pagination_is_chronological_and_bounded(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    session = db.create_session(SessionKind.PURE_CHAT, "Paged")
    for index in range(250):
        db.add_message(
            SessionKind.PURE_CHAT,
            session.id,
            "user" if index % 2 == 0 else "assistant",
            f"message-{index}",
        )

    latest, has_more = db.list_messages_page(
        SessionKind.PURE_CHAT,
        session.id,
        limit=100,
    )
    assert len(latest) == 100
    assert has_more
    assert latest[0].content == "message-150"
    assert latest[-1].content == "message-249"

    older, has_more = db.list_messages_page(
        SessionKind.PURE_CHAT,
        session.id,
        limit=100,
        before_id=latest[0].id,
    )
    assert len(older) == 100
    assert has_more
    assert older[0].content == "message-50"
    assert older[-1].content == "message-149"
    db.close()


def test_session_flags_and_message_search(tmp_path: Path) -> None:
    db = Database(tmp_path / "app.db")
    first = db.create_session(SessionKind.PURE_CHAT, "First")
    second = db.create_session(SessionKind.PURE_CHAT, "Second")
    db.add_message(SessionKind.PURE_CHAT, first.id, "user", "needle content")
    db.set_session_pinned(second.id, True)
    db.set_session_archived(second.id, True)

    sessions = db.list_sessions(SessionKind.PURE_CHAT)
    assert [session.id for session in sessions] == [first.id]
    sessions = db.list_sessions(
        SessionKind.PURE_CHAT,
        include_archived=True,
    )
    assert sessions[0].id == second.id
    assert sessions[0].pinned == 1
    assert sessions[0].archived == 1

    results = db.search_messages("needle")
    assert len(results) == 1
    assert results[0]["session_id"] == first.id
    backup = db.backup_to(tmp_path / "backup.db")
    assert backup.exists()
    db.close()
