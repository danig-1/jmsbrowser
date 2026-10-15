import curses
from dataclasses import asdict, replace

import pytest
from wcwidth import wcswidth

from jmsbrowser.config import Config, ConnectionConfig
from jmsbrowser.domain import Message, QueueInfo
from jmsbrowser.screen import render
from jmsbrowser.state import Controller
from jmsbrowser.ui import TerminalApp


@pytest.fixture
def app(monkeypatch):
    controller = Controller(Config((ConnectionConfig("Test", "artemis"),)))
    state = controller.current
    state.status = "connected"
    state.queues = [QueueInfo("Zulu", 2), QueueInfo("beta", 10), QueueInfo("Alpha", 2), QueueInfo("empty", 0)]
    state.queue_index = 1
    state.messages = [Message("ID:beta", "TextMessage", 4, "existing", 8)]
    state.messages_loaded = True
    calls = []
    monkeypatch.setattr(controller, "_submit", lambda index, operation: calls.append((operation, state.selected_queue.name)))
    app = TerminalApp(controller)
    app.calls = calls
    yield app
    controller.close()


def displayed(app):
    frame = app.last_frame = render(app.controller, 144, 42, app.view)
    return [app.controller.current.queues[index].name
            for (kind, _), index in frame.row_hits.items() if kind == "queues"]


def click_header(app, column, buttons=curses.BUTTON1_CLICKED):
    app.last_frame = render(app.controller, 144, 42, app.view)
    _, _, x, y, _ = next(action for action in app.last_frame.actions if action[:2] == ("queue_sort", column))
    app.handle_mouse(x + 1, y, buttons)


def test_default_name_sort_and_header_toggle_preserve_selected_queue_and_messages(app):
    state = app.controller.current
    original = (state.selected_queue, state.messages, state.message_index, state.generation)
    assert displayed(app) == ["Alpha", "beta", "empty", "Zulu"]
    click_header(app, "name")
    assert displayed(app) == ["Zulu", "empty", "beta", "Alpha"]
    assert app.view.queue_sort_descending
    click_header(app, "name")
    assert displayed(app) == ["Alpha", "beta", "empty", "Zulu"]
    assert not app.view.queue_sort_descending
    assert (state.selected_queue, state.messages, state.message_index, state.generation) == original
    assert state.messages is original[1] and state.messages_loaded
    assert not app.calls and app.controller.pane == 1


def test_depth_sort_is_numeric_and_ties_stay_alphabetical(app):
    click_header(app, "depth")
    assert displayed(app) == ["empty", "Alpha", "Zulu", "beta"]
    click_header(app, "depth")
    assert displayed(app) == ["beta", "Alpha", "Zulu", "empty"]
    click_header(app, "name")  # A different column starts ascending.
    assert displayed(app) == ["Alpha", "beta", "empty", "Zulu"]
    assert not app.view.queue_sort_descending and not app.calls


def test_navigation_and_mouse_selection_follow_sorted_filtered_rows_and_enter_targets_that_queue(app):
    click_header(app, "depth")
    app.view.filters[1] = "a"
    assert displayed(app) == ["Alpha", "beta"]
    app.handle_key("HOME")
    assert app.controller.current.selected_queue.name == "Alpha"
    app.handle_key("DOWN")
    assert app.controller.current.selected_queue.name == "beta"
    app.handle_key("UP")
    assert app.controller.current.selected_queue.name == "Alpha"
    assert not app.calls
    app.handle_key("ENTER")
    assert app.calls == [("queue_depth", "Alpha"), ("browse", "Alpha")]
    app.calls.clear()
    displayed(app)
    y = next(y for (kind, y), index in app.last_frame.row_hits.items()
             if kind == "queues" and app.controller.current.queues[index].name == "beta")
    x = next(region[1] + 2 for region in app.last_frame.regions if region[0] == "queues")
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    assert app.controller.current.selected_queue.name == "beta" and not app.calls
    app.handle_key("ENTER")
    assert app.calls == [("queue_depth", "beta"), ("browse", "beta")]


def test_refreshed_depths_reorder_rows_and_preserve_selected_queue_and_loaded_messages(app):
    state = app.controller.current
    click_header(app, "depth")
    messages = state.messages
    state.depth_jobs, state.all_depths_pending = 1, True
    refreshed = [QueueInfo("Zulu", 9), QueueInfo("empty", 0), QueueInfo("beta", 1), QueueInfo("Alpha", 2)]
    app.controller.results.put((0, state.epoch, state.generation, "queue_depths", [asdict(q) for q in refreshed], None))
    app.controller.poll()
    assert displayed(app) == ["empty", "beta", "Alpha", "Zulu"]
    assert state.selected_queue.name == "beta" and state.queue_index == 2
    assert state.messages is messages and state.messages_loaded and not app.calls
    app.handle_key("DOWN")
    assert state.selected_queue.name == "Alpha"


@pytest.mark.parametrize("ascii", [False, True])
@pytest.mark.parametrize("width,height", [(120, 28), (144, 42), (220, 60)])
def test_only_active_header_shows_sort_arrow_and_headers_fit_narrow_panels(app, ascii, width, height):
    controller = app.controller
    controller.config = replace(controller.config, ui=replace(controller.config.ui, ascii=ascii))
    app.view.panel_widths = (18, 24)
    for column, descending in (("name", False), ("name", True), ("depth", False), ("depth", True)):
        app.view.queue_sort, app.view.queue_sort_descending = column, descending
        frame = render(controller, width, height, app.view)
        _, queue_x, _, queue_width, _ = next(region for region in frame.regions if region[0] == "queues")
        for _, field, x, y, header_width in (action for action in frame.actions if action[0] == "queue_sort"):
            label = frame.plain_lines()[y][x:x + header_width].strip()
            title = "Queue" if field == "name" else "Depth"
            arrow = ("v" if descending else "^") if ascii else ("↓" if descending else "↑")
            assert label == title + (" " + arrow if field == column else "")
            assert x >= queue_x + 1 and x + header_width <= queue_x + queue_width - 1
        assert all(wcswidth(line) == width for line in frame.plain_lines())


def test_header_press_does_not_toggle_twice_or_activate_browse_on_double_click(app):
    click_header(app, "name", curses.BUTTON1_PRESSED)
    assert not app.view.queue_sort_descending
    click_header(app, "name", curses.BUTTON1_CLICKED)
    assert app.view.queue_sort_descending
    click_header(app, "depth", curses.BUTTON1_DOUBLE_CLICKED)
    assert app.view.queue_sort == "depth" and not app.view.queue_sort_descending
    assert not app.calls
