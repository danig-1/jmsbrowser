import curses
from dataclasses import replace
from concurrent.futures import Future

import pytest
from wcwidth import wcswidth

from jmsbrowser.config import Config, ConnectionConfig
from tools.sample_data import SampleAdapter
from jmsbrowser.domain import Consumer, ConsumerSnapshot, Message, QueueInfo, format_message_datetime
from jmsbrowser.screen import HEADERS, ViewOptions, render, short_id
from jmsbrowser.state import Controller
from jmsbrowser.ui import TerminalApp


@pytest.mark.parametrize("fullscreen", [False, True])
@pytest.mark.parametrize("tab", [0, 1, 2])
def test_data_click_copies_full_section_past_visible_rows_and_columns(controller, monkeypatch, fullscreen, tab):
    body = '{"text":"é界🙂", "long":"' + "abcdef" * 200 + '"}\n' + "offscreen\n" * 80
    message = Message("ID:copy", "TextMessage", 4, body, len(body.encode()),
                      {"JMSTimestamp": 1700000000123, **{f"header{i}": "long " * 100 for i in range(40)}},
                      {f"prop{i}": "é界🙂 " * 100 for i in range(40)})
    controller.current.messages = [message]
    app = TerminalApp(controller)
    app.view.fullscreen, app.view.body_wrap = fullscreen, False
    controller.detail_tab, controller.detail_scroll, app.view.horizontal_scroll = tab, 10, 12
    copied = []
    monkeypatch.setattr("jmsbrowser.ui.copy_text", copied.append)
    frame = app.last_frame = render(controller, 144, 42, app.view)
    _, _, x, y, _ = next(action for action in frame.actions if action[0] == "copy_detail")
    app.handle_mouse(x + 1, y, curses.BUTTON1_PRESSED)
    assert not copied
    app.handle_mouse(x + 1, y, curses.BUTTON1_CLICKED)
    assert copied == [message.body if tab == 0 else message.detail(tab)]
    assert controller.detail_scroll == 10 and app.view.horizontal_scroll == 12
    flash = render(controller, 144, 42, app.view)
    assert flash.cells[y][x][1:] == ("bg", "accent")
    assert "Copied to clipboard" in "\n".join(flash.plain_lines())
    assert app.view.fullscreen == fullscreen


@pytest.mark.parametrize("fullscreen", [False, True])
def test_tab_clicks_keyboard_navigation_controls_and_headings_never_copy(controller, monkeypatch, fullscreen):
    copied = []
    monkeypatch.setattr("jmsbrowser.ui.copy_text", copied.append)
    app = TerminalApp(controller)
    app.view.fullscreen = fullscreen
    for tab in (2, 0, 1):
        frame = app.last_frame = render(controller, 144, 42, app.view)
        action = next(hit for hit in frame.actions if hit[:2] == ("detail_tab", tab))
        app.handle_mouse(action[2], action[3], curses.BUTTON1_CLICKED)
        assert controller.detail_tab == tab
    frame = app.last_frame = render(controller, 144, 42, app.view)
    data = next(action for action in frame.actions if action[0] == "copy_detail")
    app.handle_mouse(data[2], data[3] - 2, curses.BUTTON1_CLICKED)  # Key / Value headings
    app.handle_mouse(data[2], data[3], getattr(curses, "BUTTON4_PRESSED", 0))
    app.handle_key("TAB")
    app.select_detail_tab(0)
    frame = app.last_frame = render(controller, 144, 42, app.view)
    for action in frame.actions:
        if action[0] in ("body_wrap", "body_colors"):
            app.handle_mouse(action[2], action[3], curses.BUTTON1_CLICKED)
    assert not copied and not app.view.copy_feedback


def test_copy_flash_expires_and_does_not_follow_tab_or_message_selection(controller, monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("jmsbrowser.ui.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("jmsbrowser.ui.copy_text", lambda _: None)
    app = TerminalApp(controller)
    frame = app.last_frame = render(controller, 144, 42, app.view)
    _, identity, x, y, _ = next(a for a in frame.actions if a[0] == "copy_detail")
    app.handle_mouse(x, y, curses.BUTTON1_DOUBLE_CLICKED)
    assert not app.view.fullscreen
    assert render(controller, 144, 42, app.view).cells[y][x][2] == "accent"
    clock[0] += 0.31
    assert render(controller, 144, 42, app.view).cells[y][x][2] == "bg"
    assert "Copied to clipboard" in "\n".join(render(controller, 144, 42, app.view).plain_lines())
    controller.select_message(1)
    assert "Copied to clipboard" not in "\n".join(render(controller, 144, 42, app.view).plain_lines())
    controller.select_message(0)
    app.select_detail_tab(2)
    assert "Copied to clipboard" not in "\n".join(render(controller, 144, 42, app.view).plain_lines())
    app.select_detail_tab(1)
    clock[0] = 102
    assert "Copied to clipboard" not in "\n".join(render(controller, 144, 42, app.view).plain_lines())


def test_clipboard_error_is_visible_without_a_success_flash(controller, monkeypatch):
    def fail(_):
        raise RuntimeError("Clipboard is busy; try again.")
    monkeypatch.setattr("jmsbrowser.ui.copy_text", fail)
    app = TerminalApp(controller)
    frame = app.last_frame = render(controller, 144, 42, app.view)
    _, _, x, y, _ = next(a for a in frame.actions if a[0] == "copy_detail")
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    frame = render(controller, 144, 42, app.view)
    assert "Copy failed: Clipboard is busy" in "\n".join(frame.plain_lines())
    assert frame.cells[y][x][2] == "bg"


@pytest.mark.parametrize("outcome", ["success", "error", "message", "tab", "queue", "disconnect", "modal"])
def test_truncated_body_copy_waits_for_full_read_and_discards_stale_results(controller, monkeypatch, outcome):
    message = replace(controller.current.messages[0], body="preview", truncated=True)
    controller.current.messages = [message, replace(message, message_id="ID:other")]
    app = TerminalApp(controller)
    app.select_detail_tab(0)
    future, requests, copied = Future(), [], []
    def fetch(item):
        requests.append(item)
        future.set_running_or_notify_cancel()
        return future
    monkeypatch.setattr(controller, "full_message", fetch)
    monkeypatch.setattr("jmsbrowser.ui.copy_text", copied.append)
    frame = app.last_frame = render(controller, 144, 42, app.view)
    _, _, x, y, _ = next(a for a in frame.actions if a[0] == "copy_detail")
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    app.poll_copy()
    assert requests == [message] and not copied
    assert "Reading full content" in "\n".join(render(controller, 144, 42, app.view).plain_lines())
    if outcome == "message":
        controller.select_message(1)
    elif outcome == "tab":
        app.select_detail_tab(1)
    elif outcome == "queue":
        controller.select_queue(0)
    elif outcome == "disconnect":
        controller.disconnect()
    elif outcome == "modal":
        app.view.consumer_inspector = True
    if outcome == "error":
        future.set_exception(ValueError("Message is no longer on the queue"))
    else:
        future.set_result(replace(message, body="full é界🙂\n" * 50000, truncated=False))
    app.poll_copy()
    assert app.pending_copy is None
    if outcome == "success":
        assert copied == ["full é界🙂\n" * 50000]
    elif outcome == "error":
        assert not copied and app.view.copy_error and "no longer" in app.view.copy_feedback
    else:
        assert not copied and app.view.copy_flash_until == 0


@pytest.mark.parametrize("tab", [1, 2])
def test_copy_fetches_complete_metadata_when_a_binary_value_was_previewed(controller, monkeypatch, tab):
    values = {"binary": {"type": "bytes", "size": 5000, "hex": "ab" * 4096, "truncated": True}}
    field = "headers" if tab == 1 else "properties"
    preview = replace(controller.current.messages[0], **{field: values})
    full_values = {"binary": {"type": "bytes", "size": 5000, "hex": "ab" * 5000, "truncated": False}}
    full = replace(preview, **{field: full_values})
    controller.current.messages = [preview]
    app = TerminalApp(controller)
    app.select_detail_tab(tab)
    future = Future()
    monkeypatch.setattr(controller, "full_message", lambda _: future)
    copied = []
    monkeypatch.setattr("jmsbrowser.ui.copy_text", copied.append)
    frame = app.last_frame = render(controller, 144, 42, app.view)
    _, _, x, y, _ = next(a for a in frame.actions if a[0] == "copy_detail")
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    assert not copied
    future.set_result(full)
    app.poll_copy()
    assert copied == [full.detail(tab)] and "ab" * 5000 in copied[0]


@pytest.fixture
def controller():
    config = Config((ConnectionConfig("Artemis", "artemis"), ConnectionConfig("IBM", "ibm")))
    controller = Controller(config)
    adapter = SampleAdapter(config.connections[0])
    state = controller.current
    state.status, state.queues = "connected", adapter.list_queues()
    state.queue_index = next(i for i, q in enumerate(state.queues) if q.name == "DEV.ORDERS")
    state.messages = adapter.browse("DEV.ORDERS")
    state.messages_loaded = True
    controller.pane = 2
    yield controller
    controller.close()


@pytest.mark.parametrize("width,height", [(120, 28), (144, 42), (160, 46), (220, 60)])
def test_all_requested_columns_visible_and_layout_stays_in_bounds(controller, width, height):
    frame = render(controller, width, height)
    for label in HEADERS:
        assert label in "\n".join(frame.plain_lines())
    assert all(wcswidth(line) == width for line in frame.plain_lines())
    assert controller.current.selected_message.message_id in "\n".join(frame.plain_lines())


def test_distinct_ibm_ids_remain_distinguishable_when_clipped():
    first = "ID:414d5120514d31202020202020202020c98acf6a03290040"
    second = "ID:414d5120514d31202020202020202020c98acf6a05290040"
    assert short_id(first, 18) != short_id(second, 18)
    assert wcswidth(short_id(first, 18)) <= 18


def test_tab_and_mouse_change_panels_and_select_message(controller):
    app = TerminalApp(controller)
    app.handle_key("TAB")
    assert controller.pane == 0
    app.handle_key("BACKTAB")
    assert controller.pane == 2
    frame = app.last_frame = render(controller, 144, 42)
    y = next(y for (name, y), index in frame.row_hits.items() if name == "messages" and index == 1)
    x = next(region[1] + 2 for region in frame.regions if region[0] == "messages")
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    assert controller.pane == 2 and controller.current.message_index == 1
    app.handle_key("ENTER")
    assert app.view.fullscreen
    app.handle_key("ESC")
    assert not app.view.fullscreen


def test_filter_headers_and_payload_scroll(controller):
    app = TerminalApp(controller)
    app.select_detail_tab(0)
    app.last_frame = render(controller, 120, 28)
    app.handle_key("/")
    for char in "1043":
        app.handle_key(char)
    app.handle_key("ENTER")
    assert controller.current.message_index == 1
    assert app.visible()[0] == [1]
    app.handle_key("ESC")
    assert app.visible()[0] == [0, 1, 2]
    app.handle_key("HOME")
    app.handle_key("PGDOWN")
    assert controller.detail_scroll > 0
    for _ in range(10):
        app.handle_key("PGDOWN")
    maximum = controller.detail_scroll
    app.handle_key("PGUP")
    assert controller.detail_scroll < maximum
    app.handle_key("h")
    assert controller.detail_tab == 1 and controller.detail_scroll == 0


def test_body_controls_and_wide_characters_do_not_escape_the_grid(controller):
    controller.detail_tab = 0
    state = controller.current
    state.messages = [Message("ID:test", "TextMessage", 4, "hello\x1b[2J\t界e\u0301\u202e", 20)]
    frame = render(controller, 144, 42)
    text = "\n".join(frame.plain_lines())
    assert "\x1b" not in text and "\u202e" not in text
    assert "\\x1b" in text and "\\u202e" in text
    assert all(wcswidth(line) == 144 for line in frame.plain_lines())


def test_empty_queue_and_small_terminal(controller):
    controller.current.messages = []
    assert "Queue is empty" in "\n".join(render(controller, 144, 42).plain_lines())
    assert "Resize terminal" in "\n".join(render(controller, 80, 24).plain_lines())


def test_unopened_queue_prompts_for_enter_instead_of_claiming_it_is_empty(controller):
    controller.select_queue(next(i for i, q in enumerate(controller.current.queues) if q.name == "DEV.EMPTY"))
    text = "\n".join(render(controller, 144, 42).plain_lines())
    assert "Press Enter on a queue to load messages." in text
    assert "Queue is empty" not in text
    controller.current.messages_loaded = True
    assert "Queue is empty" in "\n".join(render(controller, 144, 42).plain_lines())


def test_ascii_mode(controller):
    controller.config = replace(controller.config, ui=replace(controller.config.ui, ascii=True))
    text = "\n".join(render(controller, 144, 42).plain_lines())
    assert "●" not in text and "│" not in text


@pytest.mark.parametrize("fullscreen", [False, True])
@pytest.mark.parametrize("tab", [0, 1, 2])
def test_detail_labels_are_clickable_in_both_views(controller, fullscreen, tab):
    app = TerminalApp(controller)
    app.view.fullscreen = fullscreen
    controller.detail_scroll = 5
    app.view.horizontal_scroll = 8
    frame = app.last_frame = render(controller, 144, 42, app.view)
    _, _, x, y, width = next(hit for hit in frame.actions if hit[:2] == ("detail_tab", tab))
    assert ("Body", "Headers", "Properties")[tab] in frame.plain_lines()[y][x:x + width]
    app.handle_mouse(x + width - 1, y, curses.BUTTON1_CLICKED)
    assert controller.detail_tab == tab and controller.pane == 2
    assert controller.detail_scroll == 0 and app.view.horizontal_scroll == 0
    assert app.view.fullscreen == fullscreen


def test_expanded_detail_tab_cycles_inside_detail(controller):
    app = TerminalApp(controller)
    app.handle_key("ENTER")
    original_message = controller.current.selected_message
    for expected in (2, 0, 1, 2):
        app.handle_key("TAB")
        assert controller.detail_tab == expected
        assert app.view.fullscreen and controller.pane == 2
        assert controller.current.selected_message is original_message
    app.handle_key("BACKTAB")
    assert controller.detail_tab == 1 and app.view.fullscreen
    app.handle_key("ESC")
    app.handle_key("TAB")
    assert controller.pane == 0 and not app.view.fullscreen


@pytest.mark.parametrize("pane", [0, 1, 2])
def test_horizontal_arrows_never_change_pane_or_list_selection(controller, pane):
    app = TerminalApp(controller)
    controller.pane = pane
    controller.current.message_index = 1
    original = (controller.connection_index, controller.current.queue_index, controller.current.message_index)
    for key in ("LEFT", "RIGHT", curses.KEY_LEFT, curses.KEY_RIGHT):
        app.handle_key(key)
        assert controller.pane == pane
        assert (controller.connection_index, controller.current.queue_index, controller.current.message_index) == original


def test_message_up_down_moves_only_message_selection(controller):
    app = TerminalApp(controller)
    app.handle_key("DOWN")
    assert controller.current.message_index == 1 and controller.pane == 2
    app.handle_key("UP")
    assert controller.current.message_index == 0 and controller.pane == 2


def test_queue_enter_and_refresh_shortcut_keep_queue_focus(controller, monkeypatch):
    app = TerminalApp(controller)
    controller.pane = 1
    calls = []
    monkeypatch.setattr(controller, "refresh_queue_depth", lambda: calls.append("selected"))
    monkeypatch.setattr(controller, "browse_queue", lambda: calls.append("messages"))
    monkeypatch.setattr(controller, "refresh_queue_depths", lambda: calls.append("all"))
    original = controller.current.selected_queue
    app.handle_key("ENTER")
    assert calls == ["selected", "messages"] and controller.pane == 1
    assert controller.current.selected_queue is original
    app.handle_key("r")
    assert calls == ["selected", "messages", "all"] and controller.pane == 1
    frame = app.last_frame = render(controller, 144, 42)
    y = next(y for (name, y), index in frame.row_hits.items() if name == "queues" and index == controller.current.queue_index)
    x = next(region[1] + 2 for region in frame.regions if region[0] == "queues")
    app.handle_mouse(x, y, curses.BUTTON1_DOUBLE_CLICKED)
    assert calls == ["selected", "messages", "all", "selected", "messages"] and controller.pane == 1


def test_queue_navigation_clicks_and_filtering_never_submit_broker_requests(controller, monkeypatch):
    app = TerminalApp(controller)
    controller.pane = 1
    calls = []
    monkeypatch.setattr(controller, "_submit", lambda *args: calls.append(args))
    for key in ("UP", "DOWN", "HOME", "END", "END"):
        app.handle_key(key)
        assert not controller.current.messages and not controller.current.messages_loaded
        assert not controller.current.busy and not calls
    frame = app.last_frame = render(controller, 144, 42)
    y = next(y for (name, y), index in frame.row_hits.items() if name == "queues" and index != controller.current.queue_index)
    x = next(region[1] + 2 for region in frame.regions if region[0] == "queues")
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    app.handle_key("/")
    for char in "PAYMENTS":
        app.handle_key(char)
    app.handle_key("ENTER")  # Apply a queue filter, without activating the queue.
    assert controller.current.selected_queue.name == "DEV.PAYMENTS"
    assert not calls and not controller.current.messages and not controller.current.busy


def test_routing_and_breadcrumb_line_removed(controller):
    text = "\n".join(render(controller, 144, 42).plain_lines())
    assert "ANYCAST" not in text
    assert " / DEV.ORDERS" not in text and " / depth " not in text


def test_message_size_precedes_body_in_header_and_data(controller):
    frame = render(controller, 160, 46)
    lines = frame.plain_lines()
    header = next(line for line in lines if "MessageId" in line and "Size" in line)
    expected = ("MessageId", "Datetime", "Size", "Body")
    positions = [header.index(name) for name in expected]
    assert positions == sorted(positions)
    assert "Type" not in header and "Prio" not in header
    row = next(y for (name, y), index in frame.row_hits.items() if name == "messages" and index == 0)
    assert lines[row].index(" B") < lines[row].index('{"event"')


@pytest.mark.parametrize("width,height", [(120, 28), (144, 42), (160, 46), (220, 60)])
def test_message_datetime_uses_jms_timestamp_and_fits_its_column(controller, width, height):
    from datetime import datetime, timezone
    timestamp = 1792401234567
    message = replace(controller.current.messages[0], headers={"JMSTimestamp": timestamp, "JMSExpiration": timestamp + 999999})
    controller.current.messages[0] = message
    frame = render(controller, width, height)
    lines = frame.plain_lines()
    heading = next(line for line in lines if "MessageId" in line and "Datetime" in line)
    row = next(y for (name, y), index in frame.row_hits.items() if name == "messages" and index == 0)
    expected = datetime.fromtimestamp(timestamp / 1000, timezone.utc).astimezone().isoformat(sep=" ", timespec="seconds")[:19]
    col = heading.index("Datetime")
    assert lines[row][col:col + 19] == expected
    assert message.type not in lines[row] and str(timestamp) not in lines[row]
    assert "Tab/click panels" not in lines[-1]


@pytest.mark.parametrize("timestamp", [None, 0, "1792401234567", True, 10**100])
def test_unavailable_message_datetimes_display_na(timestamp):
    assert format_message_datetime(timestamp) == "n/a"


@pytest.mark.parametrize("width,height", [(120, 28), (160, 46), (220, 60)])
def test_message_detail_x_closes_without_changing_browser_selection(controller, width, height):
    app = TerminalApp(controller)
    app.handle_key("ENTER")
    original = (controller.pane, controller.current.queue_index, controller.current.message_index, controller.detail_tab)
    frame = app.last_frame = render(controller, width, height, app.view)
    _, _, x, y, span = next(action for action in frame.actions if action[0] == "close_detail")
    assert frame.plain_lines()[y][x:x + span] == " X " and x == width - 5
    app.handle_mouse(x + 1, y, curses.BUTTON1_PRESSED)
    assert app.view.fullscreen
    app.handle_mouse(x + 1, y, curses.BUTTON1_CLICKED)
    assert not app.view.fullscreen and app.running
    assert (controller.pane, controller.current.queue_index, controller.current.message_index, controller.detail_tab) == original


@pytest.mark.parametrize("fullscreen", [False, True])
def test_detail_tab_positions_and_widths_do_not_change(controller, fullscreen):
    positions = []
    for tab in range(3):
        controller.detail_tab = tab
        frame = render(controller, 144, 42, ViewOptions(fullscreen=fullscreen))
        hits = [hit for hit in frame.actions if hit[0] == "detail_tab"]
        positions.append(hits)
        for _, index, x, y, width in hits:
            label = frame.plain_lines()[y][x:x + width]
            assert "[" not in label and "]" not in label
            assert frame.cells[y][x][2] == ("selected" if index == tab else "bg")
    assert positions[0] == positions[1] == positions[2]


def test_selected_depth_loading_indicator_replaces_only_its_number(controller):
    state = controller.current
    state.depth_pending.add(state.selected_queue.name)
    state.depth_jobs = 1
    frame = render(controller, 144, 42)
    lines = frame.plain_lines()
    depth_x = next(line.index("Depth") + 1 for line in lines if "Queue" in line and "Depth" in line)
    for (name, y), index in frame.row_hits.items():
        if name == "queues":
            assert lines[y][depth_x:depth_x + 5].strip() == ("..." if index == state.queue_index else str(state.queues[index].depth))


@pytest.mark.parametrize("depth", [10000, 100000, 2147483647, 9223372036854775807])
@pytest.mark.parametrize("width,height,requested", [(120, 28, None), (120, 28, (18, 24)), (160, 46, None)])
def test_large_depths_show_every_digit_without_overlapping_names_or_borders(controller, depth, width, height, requested):
    controller.current.queues = [QueueInfo("APP.ORDERS", depth), QueueInfo("APP.SMALL", 2)]
    controller.current.queue_index = 0
    view = ViewOptions(panel_widths=requested)
    frame = render(controller, width, height, view)
    _, panel_x, _, panel_width, _ = next(region for region in frame.regions if region[0] == "queues")
    headers = [action for action in frame.actions if action[0] == "queue_sort"]
    assert headers[0][2] + headers[0][4] < headers[1][2]
    assert headers[1][2] + headers[1][4] <= panel_x + panel_width - 1
    for (kind, y), index in frame.row_hits.items():
        if kind != "queues":
            continue
        row = frame.plain_lines()[y][panel_x + 1:panel_x + panel_width - 1]
        expected = str(controller.current.queues[index].depth)
        assert row.rstrip().endswith(expected)
        assert "APP." in row[:row.rfind(expected)]
        assert frame.plain_lines()[y][panel_x] == frame.plain_lines()[y][panel_x + panel_width - 1] == "│"
    assert all(wcswidth(line) == width for line in frame.plain_lines())


@pytest.mark.parametrize("tab", [1, 2])
@pytest.mark.parametrize("fullscreen", [False, True])
def test_metadata_tables_right_align_keys_and_keep_them_fixed_when_values_scroll(controller, tab, fullscreen):
    values = {"Short": "one", "MuchLongerKey": "two", "界": "three"}
    controller.current.messages = [Message("ID:table", "TextMessage", 4, "body", 4, values, values)]
    controller.detail_tab = tab
    view = ViewOptions(fullscreen=fullscreen)
    frame = render(controller, 160, 46, view)
    assert any("Key" in line and "Value" in line for line in frame.plain_lines())
    rows = [line for line in frame.plain_lines() if "│ one" in line or "│ two" in line or "│ three" in line]
    assert len(rows) == 3
    separators = [wcswidth(line[:line.index("│ ", line.index("Short") if "Short" in line else line.index("MuchLongerKey") if "MuchLongerKey" in line else line.index("界"))]) for line in rows]
    assert len(set(separators)) == 1
    view.horizontal_scroll = 1
    shifted = render(controller, 160, 46, view)
    for key in values:
        original = next(line for line in frame.plain_lines() if key in line)
        scrolled = next(line for line in shifted.plain_lines() if key in line)
        assert original.index(key) == scrolled.index(key)
    assert all(wcswidth(line) == 160 for line in shifted.plain_lines())


def test_timestamp_keeps_raw_milliseconds_and_adds_local_datetime(controller):
    from datetime import datetime, timezone
    timestamp = 1791981234567
    message = Message("ID:time", "TextMessage", 4, "", 0, {"JMSTimestamp": timestamp}, {"JMSTimestamp": timestamp})
    expected = datetime.fromtimestamp(timestamp / 1000, timezone.utc).astimezone().isoformat(sep=" ", timespec="milliseconds")
    assert message.metadata(1) == [("JMSTimestamp", f"{timestamp}  ({expected})")]
    assert message.metadata(2) == [("JMSTimestamp", str(timestamp))]
    assert Message("", "Message", 0, "", 0, {"JMSTimestamp": 0}).metadata(1) == [("JMSTimestamp", "0  (not set)")]
    assert "datetime unavailable" in Message("", "Message", 0, "", 0, {"JMSTimestamp": 10**100}).detail(1)


def test_property_table_retains_types_multiline_and_empty_state(controller):
    message = Message("ID:props", "TextMessage", 4, "", 0, {}, {"number": 12, "enabled": True, "absent": None, "text": "first\nsecond"})
    assert message.metadata(2) == [("number", "12"), ("enabled", "true"), ("absent", "null"), ("text", "first"), ("", "second")]
    controller.current.messages = [replace(message, properties={})]
    controller.detail_tab = 2
    assert "No properties." in "\n".join(render(controller, 160, 46).plain_lines())


@pytest.fixture
def consumer_app(controller, monkeypatch):
    calls = []
    def refresh():
        calls.append("consumers")
        state = controller.current
        state.consumer_snapshot = ConsumerSnapshot(state.selected_queue.name, [
            Consumer("consumer-" + str(i), "orders-app-" + str(i), "127.0.0.1:5000", "app", "CORE", "color = 'red'", "conn-full-id", "session-full-id", "OK",
                     {"Extra " + str(n): "value" for n in range(40)})
            for i in range(30)
        ])
        state.consumer_status = "ready"
        return True
    monkeypatch.setattr(controller, "refresh_consumers", refresh)
    return TerminalApp(controller), calls


@pytest.mark.parametrize("detail", [False, True])
@pytest.mark.parametrize("status", ["loading", "ready", "error"])
def test_consumer_x_closes_the_whole_inspector_in_all_states(controller, consumer_app, detail, status):
    app, calls = consumer_app
    app.handle_key("c")
    app.view.consumer_detail = detail
    controller.current.consumer_status = status
    original = (controller.pane, controller.current.queue_index, controller.current.message_index)
    frame = app.last_frame = render(controller, 160, 46, app.view)
    close = next(action for action in frame.actions if action[0] == "close_consumers" and action[3] == 5)
    _, _, x, y, width = close
    assert frame.plain_lines()[y][x:x + width] == " X "
    assert not any(action[0] == "close_detail" for action in frame.actions)
    app.handle_mouse(x + 1, y, curses.BUTTON1_PRESSED)
    assert app.view.consumer_inspector
    app.handle_mouse(x + 1, y, curses.BUTTON1_CLICKED)
    assert not app.view.consumer_inspector and not app.view.consumer_detail
    assert calls == ["consumers"] and app.running
    assert (controller.pane, controller.current.queue_index, controller.current.message_index) == original


@pytest.mark.parametrize("width,height", [(120, 28), (160, 46), (220, 60)])
def test_consumer_inspector_navigation_details_and_return_preserve_browser(controller, consumer_app, width, height):
    app, calls = consumer_app
    original = (controller.pane, controller.current.queue_index, controller.current.message_index)
    app.handle_key("c")
    assert app.view.consumer_inspector and calls == ["consumers"]
    frame = app.last_frame = render(controller, width, height, app.view)
    assert "30 attached consumers" in "\n".join(frame.plain_lines())
    assert all(wcswidth(line) == width for line in frame.plain_lines())
    app.handle_key("PGDOWN")
    assert controller.current.consumer_index == min(29, frame.consumer_rows)
    app.handle_key("END")
    assert controller.current.consumer_index == 29
    app.handle_key("HOME")
    assert controller.current.consumer_index == 0
    app.handle_key("ENTER")
    assert app.view.consumer_detail
    app.last_frame = render(controller, width, height, app.view)
    app.handle_key("PGDOWN")
    assert app.view.consumer_scroll > 0
    app.handle_key("ESC")
    assert not app.view.consumer_detail and app.view.consumer_inspector
    app.handle_key("c")
    assert calls == ["consumers", "consumers"]
    app.handle_key("TAB")
    app.handle_key("ENTER")
    assert calls == ["consumers"] * 3
    app.handle_key("TAB")
    app.handle_key("ENTER")
    assert not app.view.consumer_inspector
    assert (controller.pane, controller.current.queue_index, controller.current.message_index) == original


def test_consumer_inspector_mouse_and_modal_isolation(controller, consumer_app):
    app, calls = consumer_app
    original = (controller.pane, controller.current.queue_index, controller.current.message_index)
    app.handle_key("c")
    assert app.view.consumer_inspector
    frame = app.last_frame = render(controller, 160, 46, app.view)
    app.handle_mouse(2, 6, curses.BUTTON1_CLICKED)
    assert (controller.pane, controller.current.queue_index, controller.current.message_index) == original
    row = next(y for (name, y), i in frame.row_hits.items() if name == "consumers" and i == 1)
    app.handle_mouse(10, row, curses.BUTTON1_DOUBLE_CLICKED)
    assert controller.current.consumer_index == 1 and app.view.consumer_detail
    app.last_frame = render(controller, 160, 46, app.view)
    text = "\n".join(app.last_frame.plain_lines())
    assert "conn-full-id" in text and "session-full-id" in text and "color = 'red'" in text
    action = next(hit for hit in app.last_frame.actions if hit[0] == "refresh_consumers")
    app.handle_mouse(action[2], action[3], curses.BUTTON1_CLICKED)
    assert len(calls) == 2
    action = next(hit for hit in app.last_frame.actions if hit[0] == "close_consumers")
    app.handle_mouse(action[2], action[3], curses.BUTTON1_CLICKED)
    assert not app.view.consumer_inspector


@pytest.mark.parametrize("status,expected", [("loading", "Loading queue consumer metadata"), ("ready", "0 attached consumers"), ("error", "Unavailable")])
def test_consumer_states_are_distinct_and_work_without_messages(controller, status, expected):
    controller.current.messages = []
    controller.current.consumer_status = status
    controller.current.consumer_error = "Not authorized"
    controller.current.consumer_snapshot = ConsumerSnapshot(controller.current.selected_queue.name)
    frame = render(controller, 160, 46, ViewOptions(consumer_inspector=True))
    text = "\n".join(frame.plain_lines())
    assert expected in text
    if status == "error":
        assert "Not authorized" in text and "0 attached consumers" not in text


@pytest.mark.parametrize("fullscreen", [False, True])
def test_headers_are_the_default_first_tab_followed_by_properties_and_body(controller, fullscreen):
    assert controller.detail_tab == 1
    frame = render(controller, 160, 46, ViewOptions(fullscreen=fullscreen))
    tabs = [action for action in frame.actions if action[0] == "detail_tab"]
    assert [action[1] for action in tabs] == [1, 2, 0]
    for action, label in zip(tabs, ("Headers", "Properties", "Body")):
        _, tab, x, y, width = action
        assert frame.plain_lines()[y][x:x + width].strip() == label
        assert frame.cells[y][x][2] == ("selected" if tab == 1 else "bg")
    assert "JMSMessageID" in "\n".join(frame.plain_lines())


@pytest.mark.parametrize("width,height", [(120, 28), (144, 42), (160, 46), (220, 60), (160, 47)])
def test_message_list_and_detail_each_occupy_half_the_panel(controller, width, height):
    controller.current.messages *= 30
    frame = render(controller, width, height)
    _, _, top, _, panel_height = next(region for region in frame.regions if region[0] == "messages")
    assert top + panel_height == height - 1  # Panels reclaim the former status area.
    _, _, detail_top, _, detail_height = next(region for region in frame.regions if region[0] == "detail")
    list_height = detail_top - top - 2  # outer title/border row and shared separator
    assert abs(list_height - detail_height) <= 1
    assert list_height + detail_height + 1 == panel_height - 2
    message_rows = [y for name, y in frame.row_hits if name == "messages"]
    assert max(message_rows) == detail_top - 2
    assert len(message_rows) > 3
    assert all(wcswidth(line) == width for line in frame.plain_lines())


@pytest.mark.parametrize("fullscreen", [False, True])
def test_queue_buttons_removed_and_keyboard_hints_retained(controller, fullscreen):
    frame = render(controller, 160, 46, ViewOptions(fullscreen=fullscreen))
    assert not any(action[0] in ("refresh_queues", "open_consumers") for action in frame.actions)
    text = "\n".join(frame.plain_lines())
    assert "[ Refresh ]" not in text and "[ Consumers ]" not in text
    footer = frame.plain_lines()[-1]
    assert "c consumers" in footer and "r refresh" in footer


def test_consumer_c_refreshes_and_r_has_no_action(controller, consumer_app):
    app, calls = consumer_app
    app.handle_key("c")
    assert len(calls) == 1
    original = (controller.pane, controller.current.queue_index, controller.current.message_index)
    app.handle_key("r")
    assert len(calls) == 1
    app.handle_key("c")
    assert len(calls) == 2 and app.view.consumer_inspector
    frame = app.last_frame = render(controller, 120, 28, app.view)
    text = "\n".join(frame.plain_lines())
    assert "c: refresh consumers" in text and "r: refresh consumers" not in text
    assert "c refresh consumers" in frame.plain_lines()[-1]
    assert "r refresh" not in frame.plain_lines()[-1]
    assert (controller.pane, controller.current.queue_index, controller.current.message_index) == original


def drag(app, index, delta):
    frame = app.last_frame = render(app.controller, 160, 46, app.view)
    _, x, y, _, _ = frame.dividers[index]
    x += 1
    app.handle_mouse(x, y + 10, curses.BUTTON1_PRESSED)
    # Windows PDCurses may report mouse motion with an empty button state.
    app.handle_mouse(x + delta, y + 11, 0)
    app.last_frame = render(app.controller, 160, 46, app.view)
    app.handle_mouse(x + delta, y + 11, curses.BUTTON1_RELEASED)


@pytest.mark.parametrize("index,delta", [(0, 4), (0, -8), (1, 12), (1, -8)])
def test_drag_changes_only_adjacent_widths_and_preserves_selection(controller, index, delta):
    app = TerminalApp(controller)
    original = (controller.pane, controller.connection_index, controller.current.queue_index, controller.current.message_index)
    before = render(controller, 160, 46).panel_widths
    drag(app, index, delta)
    frame = render(controller, 160, 46, app.view)
    assert frame.panel_widths != before
    if index == 0:
        assert sum(frame.panel_widths) == sum(before)
    else:
        assert frame.panel_widths[0] == before[0]
    assert app.view.drag_divider is None
    assert original == (controller.pane, controller.connection_index, controller.current.queue_index, controller.current.message_index)
    header = next(line for line in frame.plain_lines() if all(label in line for label in HEADERS))
    assert header
    # Hit regions are rebuilt at the new positions.
    y = next(y for (name, y), i in frame.row_hits.items() if name == "messages" and i == 1)
    x = next(region[1] + 2 for region in frame.regions if region[0] == "messages")
    app.last_frame = frame
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    assert controller.pane == 2 and controller.current.message_index == 1


@pytest.mark.parametrize("index,delta", [(0, -1000), (0, 1000), (1, -1000), (1, 1000)])
def test_drag_limits_resize_and_double_click_reset(controller, index, delta):
    from jmsbrowser.screen import MIN_PANEL_WIDTHS
    app = TerminalApp(controller)
    drag(app, index, delta)
    for width, height in ((120, 28), (160, 46), (220, 60)):
        frame = render(controller, width, height, app.view)
        left, middle = frame.panel_widths
        assert all(actual >= minimum for actual, minimum in zip((left, middle, width - left - middle - 2), MIN_PANEL_WIDTHS))
        assert all(wcswidth(line) == width for line in frame.plain_lines())
        assert all(label in "\n".join(frame.plain_lines()) for label in HEADERS)
        _, _, top, _, panel_height = next(r for r in frame.regions if r[0] == "messages")
        _, _, detail_top, _, detail_height = next(r for r in frame.regions if r[0] == "detail")
        assert abs((detail_top - top - 2) - detail_height) <= 1
    app.last_frame = render(controller, 160, 46, app.view)
    _, x, y, _, _ = app.last_frame.dividers[index]
    app.handle_mouse(x + 1, y + 2, curses.BUTTON1_DOUBLE_CLICKED)
    assert app.view.panel_widths is None


def test_escape_cancels_drag_and_motion_never_changes_focus(controller):
    app = TerminalApp(controller)
    app.last_frame = render(controller, 160, 46, app.view)
    app.handle_mouse(2, 6, getattr(curses, "REPORT_MOUSE_POSITION", 0))
    assert controller.pane == 2
    _, x, y, _, _ = app.last_frame.dividers[1]
    app.handle_mouse(x + 1, y + 2, curses.BUTTON1_PRESSED)
    app.handle_mouse(x + 9, y + 2, getattr(curses, "REPORT_MOUSE_POSITION", 0))
    assert app.view.panel_widths is not None
    app.handle_key("ESC")
    assert app.view.panel_widths is None and app.view.drag_divider is None
    assert controller.pane == 2


@pytest.mark.parametrize("pane", [0, 1, 2])
def test_active_title_and_row_are_distinct_from_inactive_selection(controller, pane):
    controller.pane = pane
    frame = render(controller, 160, 46)
    for index, name in enumerate(("connections", "queues", "messages")):
        _, x, y, _, _ = next(r for r in frame.regions if r[0] == name)
        assert frame.cells[y][x + 2][2] == ("accent" if pane == index else "bar")
        selected_index = (controller.connection_index, controller.current.queue_index, controller.current.message_index)[index]
        row = next(y for (kind, y), i in frame.row_hits.items() if kind == name and i == selected_index)
        assert frame.cells[row][x + 2][2] == ("selected" if pane == index else "bar")
        assert (">" in frame.plain_lines()[row][x:x + 4]) == (pane == index)
    assert "FOCUS" not in "\n".join(frame.plain_lines())


@pytest.mark.parametrize("fullscreen", [False, True])
@pytest.mark.parametrize("payload", ['{"note": "' + 'Long text ' * 300 + '"}', '<event id="1">' + 'Long text ' * 300 + '</event>', 'Long text ' * 300])
def test_body_wrap_scroll_controls_and_colors_in_both_views(controller, fullscreen, payload):
    app = TerminalApp(controller)
    app.view.fullscreen = fullscreen
    controller.detail_tab = 0
    message = Message("ID:wrapped", "TextMessage", 4, payload, len(payload))
    controller.current.messages = [message]
    frame = app.last_frame = render(controller, 120, 28, app.view)
    assert frame.detail_total_rows > frame.detail_height
    app.scroll_detail(10**9)
    assert controller.detail_scroll == frame.detail_total_rows - frame.detail_height
    frame = app.last_frame = render(controller, 120, 28, app.view)
    assert "Long text" in "\n".join(frame.plain_lines())
    assert all(wcswidth(line) == 120 for line in frame.plain_lines())
    original_selection = controller.current.message_index
    app.handle_key("]")
    assert app.view.horizontal_scroll == 0
    app.handle_key("s")
    assert controller.current.message_index == original_selection
    frame = app.last_frame = render(controller, 120, 28, app.view)
    if fullscreen:
        app.handle_key("w")
        assert app.view.body_colors and app.view.body_wrap
        assert not any(action[0] in ("body_wrap", "body_colors") for action in frame.actions)
        assert "w wrap" not in frame.plain_lines()[-1] and "s color" not in frame.plain_lines()[-1]
        assert message.body == payload
        return
    assert not app.view.body_colors
    _, _, x, y, _ = next(action for action in frame.actions if action[0] == "body_wrap")
    # A press then a click toggles once, not twice.
    app.handle_mouse(x, y, curses.BUTTON1_PRESSED)
    assert app.view.body_wrap
    app.handle_mouse(x, y, curses.BUTTON1_CLICKED)
    assert not app.view.body_wrap and controller.detail_scroll == 0
    app.handle_key("]")
    assert app.view.horizontal_scroll == 8
    app.handle_key("w")
    assert app.view.body_wrap and app.view.horizontal_scroll == 0
    assert message.body == payload


def test_expanded_body_always_wraps_and_colors_even_if_inline_settings_are_off(controller):
    controller.current.messages = [Message("ID:colors", "TextMessage", 4,
                                         '{"key":"' + "Long text " * 300 + '"}', 3010)]
    controller.detail_tab = 0
    app = TerminalApp(controller)
    app.view.body_wrap = app.view.body_colors = False
    app.view.horizontal_scroll = 32
    app.handle_key("ENTER")
    frame = app.last_frame = render(controller, 120, 28, app.view)
    assert frame.detail_total_rows > frame.detail_height
    assert any(fg == "green" for row in frame.cells for _, fg, _ in row)
    assert not any(action[0] in ("body_wrap", "body_colors") for action in frame.actions)
    assert "Wrap:" not in "\n".join(frame.plain_lines()) and "Color:" not in "\n".join(frame.plain_lines())
    for key in ("w", "s", "]"):
        app.handle_key(key)
    assert not app.view.body_wrap and not app.view.body_colors
    assert app.view.horizontal_scroll == 32
    assert render(controller, 120, 28, app.view).plain_lines() == frame.plain_lines()
    app.scroll_detail(10**9)
    assert controller.detail_scroll == frame.detail_total_rows - frame.detail_height
    app.handle_key("ESC")
    assert not app.view.fullscreen and not app.view.body_wrap and not app.view.body_colors


def test_body_colors_are_optional_and_controls_are_absent_on_metadata_tabs(controller):
    controller.current.messages = [Message("ID:colors", "TextMessage", 4, '{"key": "value", "flag": true}', 30)]
    controller.detail_tab = 0
    view = ViewOptions()
    frame = render(controller, 160, 46, view)
    _, detail_x, detail_y, detail_width, _ = next(r for r in frame.regions if r[0] == "detail")
    content_row = next(i for i, line in enumerate(frame.plain_lines()) if i >= detail_y + 4 and '"key"' in line)
    assert any(fg == "green" for _, fg, _ in frame.cells[content_row])
    view.body_colors = False
    plain = render(controller, 160, 46, view)
    assert not any(fg in ("green", "accent") for _, fg, _ in plain.cells[content_row][detail_x:detail_x + detail_width])
    for tab in (1, 2):
        controller.detail_tab = tab
        frame = render(controller, 160, 46, view)
        assert not any(action[0] in ("body_wrap", "body_colors") for action in frame.actions)


def test_modals_and_fullscreen_have_no_draggable_dividers(controller, consumer_app):
    app, _ = consumer_app
    app.view.panel_widths = (22, 44)
    app.handle_key("c")
    frame = app.last_frame = render(controller, 160, 46, app.view)
    assert not frame.dividers
    app.handle_mouse(22, 10, curses.BUTTON1_PRESSED)
    assert app.view.drag_divider is None and app.view.panel_widths == (22, 44)
    app.handle_key("ESC")
    app.handle_key("n")
    assert not render(controller, 160, 46, app.view).dividers
    app.handle_key("ESC")
    app.view.fullscreen = True
    assert not render(controller, 160, 46, app.view).dividers


def test_long_unwrapped_xml_keeps_colored_viewport_and_can_scroll(controller):
    payload = '<events>' + '<item id="1">value</item>' * 1500 + '</events>'
    controller.current.messages = [Message("ID:xml", "TextMessage", 4, payload, len(payload))]
    controller.detail_tab = 0
    view = ViewOptions(body_wrap=False)
    frame = render(controller, 120, 28, view)
    _, x, y, width, _ = next(r for r in frame.regions if r[0] == "detail")
    body = frame.plain_lines()[y + 4][x + 7:x + width - 1]
    assert body.startswith("<events>") and body.endswith("…")
    assert any(role == "green" for _, role, _ in frame.cells[y + 4][x + 7:x + width - 1])
    view.horizontal_scroll = len(payload) - len("</events>")
    shifted = render(controller, 120, 28, view)
    assert shifted.plain_lines()[y + 4][x + 7:x + width - 1].strip() == "</events>"
    assert all(wcswidth(line) == 120 for line in shifted.plain_lines())
