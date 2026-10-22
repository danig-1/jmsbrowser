from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import replace
import curses
import json
from types import SimpleNamespace

import pytest
from wcwidth import wcswidth

from jmsbrowser.config import Config, ConnectionConfig
from jmsbrowser.domain import ConsumerSnapshot, Message
from jmsbrowser.message_export import needs_full_message, write_export
from jmsbrowser.screen import render
from jmsbrowser.state import Controller
from jmsbrowser.ui import TerminalApp, navigation_key
from tools.sample_data import SampleAdapter


@pytest.fixture
def app():
    controller = Controller(Config((ConnectionConfig("Artemis", "artemis", password="secret"),
                                    ConnectionConfig("IBM", "ibm"))))
    state = controller.current
    adapter = SampleAdapter(state.config)
    state.status, state.queues = "connected", adapter.list_queues()
    state.queue_index = next(i for i, queue in enumerate(state.queues) if queue.name == "DEV.ORDERS")
    state.messages, state.messages_loaded = adapter.browse("DEV.ORDERS"), True
    controller.pane = 2
    app = TerminalApp(controller)
    yield app
    controller.close()


def frame(app, width=144, height=42):
    app.last_frame = render(app.controller, width, height, app.view)
    return app.last_frame


def find(app, query):
    app.handle_key("\x06")
    for char in query:
        app.handle_key(char)
    app.handle_key("ENTER")


def click_footer(app, key):
    rendered = frame(app)
    _, _, x, y, _ = next(a for a in rendered.actions if a[:2] == ("footer_key", key))
    app.handle_mouse(x, y, curses.BUTTON1_PRESSED)
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)


def highlighted_data(rendered):
    return "".join(rendered.cells[y][column][0]
                   for action, _, x, y, width in rendered.actions if action == "copy_detail"
                   for column in range(x, x + width)
                   if rendered.cells[y][column][1:] == ("bg", "accent"))


def test_literal_search_matches_all_sections_and_wraps_in_both_directions(app):
    app.controller.current.messages = [Message("ID:find", "TextMessage", 4, "NEEDLE\nbody needle", 18,
                                             {"needleKey": "needle"}, {"property": "needle"})]
    frame(app)
    find(app, "needle")
    search = app.view.search
    assert len(search.matches) == 5
    assert [match.tab for match in search.matches] == [1, 1, 2, 0, 0]
    for _ in range(5):
        app.handle_key("F3")
    assert search.current == 0 and app.controller.detail_tab == 1
    app.handle_key("SHIFT_F3")
    assert search.current == 4 and app.controller.detail_tab == 0
    app.handle_key("ESC")
    assert app.view.search is None and not app.view.fullscreen


@pytest.mark.parametrize("query", ["[a]+", "é界🙂", "Needle"])
@pytest.mark.parametrize("fullscreen", [False, True])
@pytest.mark.parametrize("wrap", [False, True])
def test_search_reveals_offscreen_matches_with_unicode_and_wrapping(app, query, fullscreen, wrap):
    body = "first\n" * 70 + "界" * 90 + query + " trailing " * 3
    app.controller.current.messages = [Message("ID:long", "TextMessage", 4, body, len(body.encode()))]
    app.controller.detail_tab = 0
    app.view.fullscreen, app.view.body_wrap = fullscreen, wrap
    original = app.controller.current.selected_message
    frame(app, 120, 28)
    find(app, query)
    rendered = frame(app, 120, 28)
    assert len(app.view.search.matches) == 1 and app.controller.detail_scroll > 0
    assert query.casefold() in highlighted_data(rendered).casefold()
    assert all(wcswidth(line) == 120 for line in rendered.plain_lines())
    assert app.controller.current.selected_message is original
    assert original.body == body
    if fullscreen:
        app.handle_key("ESC")
        assert app.view.fullscreen
        app.handle_key("ESC")
        assert not app.view.fullscreen


@pytest.mark.parametrize("field", ["key", "value"])
def test_search_reveals_long_metadata_keys_and_values(app, field):
    key = "prefix" * 25 + "needle" if field == "key" else "short"
    value = "prefix" * 25 + "needle" if field == "value" else "value"
    app.controller.current.messages = [Message("ID:meta", "TextMessage", 4, "body", 4, {}, {key: value})]
    frame(app, 120, 28)
    find(app, "needle")
    rendered = frame(app, 120, 28)
    assert app.controller.detail_tab == 2
    assert "needle" in "\n".join(rendered.plain_lines())
    assert "needle" in highlighted_data(rendered)


def test_match_spanning_two_wrapped_rows_is_one_match_and_highlights_both_parts(app):
    app.controller.detail_tab = 0
    rendered = frame(app, 120, 28)
    columns = next(region[3] for region in rendered.regions if region[0] == "detail") - 8
    body = "z" * (columns - 2) + "needle" + "z" * 20
    app.controller.current.messages = [Message("ID:wrap", "TextMessage", 4, body, len(body))]
    find(app, "needle")
    rendered = frame(app, 120, 28)
    assert len(app.view.search.matches) == 1 and highlighted_data(rendered) == "needle"


def test_search_query_is_text_and_zero_matches_are_reported_without_list_navigation(app):
    frame(app)
    app.handle_key("\x06")
    for key in "qnr []":
        app.handle_key(key)
    app.handle_key("ENTER")
    assert app.running and app.view.search.query == "qnr []"
    assert not app.view.search.matches and app.view.composer is None
    assert "No matches" in "\n".join(frame(app).plain_lines())
    app.handle_key("DOWN")
    assert app.view.search is None


@pytest.mark.parametrize("outcome", ["complete", "gone", "changed", "incomplete"])
def test_search_fetches_full_message_asynchronously_and_discards_stale_results(app, monkeypatch, outcome):
    state = app.controller.current
    preview = replace(state.messages[0], body="preview", truncated=True)
    state.messages[0] = preview
    future = Future()
    future.set_running_or_notify_cancel()
    monkeypatch.setattr(app.controller, "full_message", lambda message: future)
    frame(app)
    find(app, "offscreen")
    assert app.view.search.pending
    assert "Reading full message" in "\n".join(frame(app).plain_lines())
    if outcome == "changed":
        app.controller.select_message(1)
    if outcome == "gone":
        future.set_exception(ValueError("Message no longer exists"))
    else:
        future.set_result(replace(preview, body="body\noffscreen", truncated=outcome == "incomplete"))
    app.poll_search()
    if outcome == "changed":
        assert app.view.search is None and state.messages[0] is preview
    elif outcome == "complete":
        assert not app.view.search.pending and len(app.view.search.matches) == 1
        assert state.messages[0].body == "body\noffscreen"
    else:
        assert app.view.search.error and not app.view.search.matches


def test_space_toggle_advances_and_shift_arrows_extend_then_shrink_selection(app):
    state = app.controller.current
    keys = [message.selection_key for message in state.messages]
    app.handle_key(" ")
    assert state.marked_messages == {keys[0]} and state.message_index == 1
    app.handle_key("SHIFT_DOWN")
    assert state.marked_messages == set(keys) and state.message_index == 2
    app.handle_key("SHIFT_UP")
    assert state.marked_messages == set(keys[:2]) and state.message_index == 1
    app.handle_key(" ")
    assert state.marked_messages == {keys[0]} and state.message_index == 2
    app.handle_key("ESC")
    assert not state.marked_messages


def test_range_selection_uses_only_visible_rows_and_never_changes_queue_or_connection(app):
    state = app.controller.current
    state.messages = [Message(f"ID:{i}", "TextMessage", 4, "yes" if i % 2 == 0 else "no", 3) for i in range(8)]
    app.view.filters[2] = "yes"
    original = (app.controller.connection_index, state.queue_index)
    app.handle_key("SHIFT_DOWN")
    app.handle_key("SHIFT_DOWN")
    assert state.marked_messages == {"ID:0", "ID:2", "ID:4"} and state.message_index == 4
    assert (app.controller.connection_index, state.queue_index) == original
    app.handle_key("ESC")  # clear filter first
    assert state.marked_messages == {"ID:0", "ID:2", "ID:4"}


def test_shift_mouse_wheel_extends_selection(app):
    rendered = frame(app)
    _, x, y, _, _ = next(region for region in rendered.regions if region[0] == "messages")
    app.handle_mouse(x + 4, y + 5, curses.BUTTON_SHIFT | curses.BUTTON5_PRESSED)
    assert len(app.controller.current.marked_messages) == 2


def test_terminal_shift_key_codes_are_decoded():
    assert navigation_key(curses.KEY_SF) == "SHIFT_DOWN"
    assert navigation_key(curses.KEY_SR) == "SHIFT_UP"
    assert navigation_key(curses.KEY_F15) == "SHIFT_F3"


@pytest.mark.parametrize("pane", [0, 1])
def test_marking_export_and_search_are_inactive_outside_messages(app, pane):
    app.controller.pane = pane
    for key in (" ", "SHIFT_DOWN", "e", "\x06"):
        app.handle_key(key)
    assert not app.controller.current.marked_messages and app.view.search is None and app.view.export_dialog is None


def test_selection_is_cleared_on_queue_change_and_preserved_only_for_surviving_ids_on_refresh(app):
    c, state = app.controller, app.controller.current
    app.handle_key("SHIFT_DOWN")
    old = state.messages
    c.results.put((0, state.epoch, state.generation, "browse", [old[1].to_dict(), old[2].to_dict()], None))
    c.poll()
    assert state.marked_messages == {old[1].selection_key}
    c.select_queue(0)
    assert not state.marked_messages


@pytest.mark.parametrize("width", [120, 144, 220])
def test_footer_buttons_are_clickable_separated_and_quit_stays_right(app, width):
    app.handle_key(" ")
    rendered = frame(app, width)
    actions = sorted((a for a in rendered.actions if a[0] == "footer_key"), key=lambda a: a[2])
    assert actions[-1][1] == "QUIT" and actions[-1][2] + actions[-1][4] == width - 1
    assert "e export (1)" in rendered.plain_lines()[-1]
    for left, right in zip(actions, actions[1:]):
        assert left[2] + left[4] < right[2]
        assert "│" in rendered.plain_lines()[-1][left[2] + left[4]:right[2]]
    click_footer(app, "e")
    assert app.view.export_dialog and len(app.view.export_dialog.messages) == 1
    click_footer(app, "ESC")
    assert app.view.export_dialog is None
    click_footer(app, "QUIT")
    assert not app.running


def test_footer_actions_follow_context_and_prompt_buttons_work(app, monkeypatch):
    c = app.controller
    for pane in (0, 1, 2):
        c.pane = pane
        text = frame(app).plain_lines()[-1]
        assert ("Enter connect" in text) is False
        assert ("c consumers" in text) == (pane == 1)
        assert ("Space mark" in text) == (pane == 2)
        assert "e export" not in text
    click_footer(app, "/")
    for char in "1043":
        app.handle_key(char)
    click_footer(app, "ENTER")
    assert app.view.filters[2] == "1043" and c.current.message_index == 1
    calls = []
    monkeypatch.setattr(c, "refresh", lambda: calls.append("refresh"))
    click_footer(app, "r")
    assert calls == ["refresh"]
    c.pane = 0
    c.current.status = "disconnected"
    assert "Enter connect" in frame(app).plain_lines()[-1]
    assert "r refresh" not in frame(app).plain_lines()[-1]


def test_no_match_filter_hides_and_disables_message_actions(app):
    app.view.filters[2] = "does-not-exist"
    text = frame(app).plain_lines()[-1]
    assert "Space mark" not in text and "Enter detail" not in text and "^F find" not in text
    for key in (" ", "ENTER", "\x06"):
        app.handle_key(key)
    assert not app.view.fullscreen and app.view.search is None and not app.controller.current.marked_messages


@pytest.mark.parametrize("tab", [0, 1, 2])
def test_export_writes_full_bodies_and_native_metadata_without_credentials(app, tmp_path, tab):
    state = app.controller.current
    original = replace(state.messages[0], body="é界🙂\n" * 1000,
                       headers={"JMSPriority": 4}, properties={"enabled": True, "amount": 1.25, "count": 3})
    message = replace(original, **({"body": "preview", "truncated": True} if tab == 0 else
                                 {"headers" if tab == 1 else "properties": {"binary": {"type": "bytes", "truncated": True}}}))
    state.executor = ThreadPoolExecutor(max_workers=1)
    calls = []
    def request(operation, **options):
        calls.append((operation, options))
        return original.to_dict()
    state.client = SimpleNamespace(request=request, close=lambda: None)
    path = tmp_path / "export.json"
    result = app.controller.export_messages([message], path).result(timeout=3)
    assert result == path
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["messages"] == [original.to_dict()]
    assert data["connection"] == "Artemis" and data["queue"] == "DEV.ORDERS"
    assert "secret" not in path.read_text(encoding="utf-8")
    assert calls == [("full_message", {"queue": "DEV.ORDERS", "message_id": original.message_id})]


def test_export_is_non_overwriting_and_incomplete_messages_never_create_files(app, tmp_path):
    message = app.controller.current.messages[0]
    path = tmp_path / "existing.json"
    path.write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        write_export(path, "Artemis", "Q", [message])
    assert path.read_text() == "keep"
    incomplete = replace(message, truncated=True)
    assert needs_full_message(incomplete)
    with pytest.raises(ValueError, match="full content"):
        write_export(tmp_path / "new.json", "Artemis", "Q", [incomplete])
    assert not (tmp_path / "new.json").exists()


def test_export_cleans_up_a_failed_write(app, tmp_path, monkeypatch):
    def broken_dump(payload, output, **options):
        output.write('{"partial":')
        raise OSError("Disk full")
    monkeypatch.setattr("jmsbrowser.message_export.json.dump", broken_dump)
    path = tmp_path / "failed.json"
    with pytest.raises(OSError, match="Disk full"):
        write_export(path, "Artemis", "Q", app.controller.current.messages)
    assert not path.exists()


def test_export_dialog_uses_snapshot_handles_errors_and_quietly_ignores_duplicate_clicks(app, monkeypatch, tmp_path):
    app.handle_key("SHIFT_DOWN")
    click_footer(app, "e")
    dialog = app.view.export_dialog
    dialog.path.text = str(tmp_path / "out.json")
    dialog.path.cursor = len(dialog.path.text)
    future, calls = Future(), []
    def export(messages, path):
        calls.append((messages, path))
        future.set_running_or_notify_cancel()
        return future
    monkeypatch.setattr(app.controller, "export_messages", export)
    click_footer(app, "EXPORT")
    app.handle_key("EXPORT")
    app.handle_key("ESC")
    assert len(calls) == 1 and dialog.working and app.view.export_dialog is dialog
    future.set_exception(ValueError("Message disappeared"))
    rendered = frame(app)
    assert "Message disappeared" in "\n".join(rendered.plain_lines())
    assert len(app.controller.current.marked_messages) == 2
    app.handle_key("ESC")
    assert app.view.export_dialog is None


def test_export_dialog_rejects_a_changed_queue_before_starting(app, monkeypatch):
    app.handle_key(" ")
    app.handle_key("e")
    dialog = app.view.export_dialog
    app.controller.select_queue(0)
    calls = []
    monkeypatch.setattr(app.controller, "export_messages", lambda *args: calls.append(args))
    app.handle_key("EXPORT")
    assert not calls and "queue or connection changed" in dialog.error


def test_modal_footer_actions_use_the_active_screen_and_never_send_before_review(app, monkeypatch):
    app.open_composer()
    composer = app.view.composer
    calls = []
    monkeypatch.setattr(app.controller, "send_message", lambda *args: calls.append(args))
    click_footer(app, "F4")
    assert composer.prompt_kind == "save_draft"
    click_footer(app, "ESC")
    assert composer.prompt is None and app.view.composer is composer
    click_footer(app, "F2")
    assert composer.stage == "preview" and not calls
    click_footer(app, "COMPOSER_EDIT")
    assert composer.stage == "edit" and not calls
    click_footer(app, "ESC")
    assert app.view.composer is None
    state = app.controller.current
    state.consumer_snapshot, state.consumer_status = ConsumerSnapshot(state.selected_queue.name, []), "ready"
    app.view.consumer_inspector = True
    monkeypatch.setattr(app.controller, "refresh_consumers", lambda: calls.append("consumers"))
    click_footer(app, "c")
    assert calls == ["consumers"]
    assert "Space mark" not in frame(app).plain_lines()[-1]
    click_footer(app, "ESC")
    assert not app.view.consumer_inspector


@pytest.mark.parametrize("provider", ["artemis", "ibm"])
def test_search_and_export_use_the_existing_worker_protocol_without_consuming(sample_worker, tmp_path, provider):
    from jmsbrowser.config import UIConfig
    from tests.test_state_workers import wait_until
    c = Controller(Config((ConnectionConfig("Test", provider, auto_connect=True),), UIConfig(max_body_bytes=5)))
    app = TerminalApp(c)
    try:
        wait_until(c, lambda: c.current.status == "connected")
        c.select_queue(next(i for i, q in enumerate(c.current.queues) if q.name == "DEV.ORDERS"))
        c.browse_queue()
        wait_until(c, lambda: c.current.messages_loaded)
        c.pane = 2
        depth = c.current.selected_queue.depth
        app.handle_key("SHIFT_DOWN")
        result = c.export_messages(c.current.export_selection, tmp_path / "worker.json").result(timeout=3)
        exported = json.loads(result.read_text(encoding="utf-8"))
        assert len(exported["messages"]) == 2 and all(not m["truncated"] for m in exported["messages"])
        frame(app)
        find(app, "order.created")
        def ready():
            app.poll_search()
            return app.pending_search is None
        wait_until(c, ready)
        assert any(match.tab == 0 for match in app.view.search.matches)
        c.refresh_queue_depth()
        wait_until(c, lambda: c.current.depth_jobs == 0)
        assert c.current.selected_queue.depth == depth
    finally:
        c.close()
