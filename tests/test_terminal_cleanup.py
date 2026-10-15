from types import SimpleNamespace

import pytest

from jmsbrowser import ui


@pytest.mark.parametrize("failure", [None, KeyboardInterrupt(), RuntimeError("UI failed")],
                         ids=["quit", "interrupt", "ui-error"])
@pytest.mark.parametrize("cursor_supported", [True, False])
def test_exit_clears_frame_before_terminal_teardown_and_closes_workers(monkeypatch, failure, cursor_supported):
    screen = SimpleNamespace(content="", background=None, attribute=None, displayed="")
    events = []
    mouse = []
    def clear():
        screen.content = ""
    def refresh():
        screen.displayed = screen.content
        events.append("repaint")
    window = SimpleNamespace(
        bkgd=lambda char, attr: setattr(screen, "background", (char, attr)),
        attrset=lambda attr: setattr(screen, "attribute", attr), clear=clear, refresh=refresh)
    def loop(self, _):
        screen.content = screen.displayed = "JMS BROWSER\n1 Connections\n2 Queues\n3 Messages"
        if failure:
            raise failure
    def wrapper(callback):
        try:
            callback(window)
        finally:
            # The blank frame must be flushed while curses still owns the screen.
            assert screen.displayed == ""
            assert screen.background == (" ", ui.curses.A_NORMAL)
            assert screen.attribute == ui.curses.A_NORMAL
            events.append("endwin")
    def show_cursor(visible):
        assert visible == 1
        if not cursor_supported:
            raise ui.curses.error("Cursor visibility unavailable")
    monkeypatch.setattr(ui.TerminalApp, "loop", loop)
    monkeypatch.setattr(ui.curses, "wrapper", wrapper)
    monkeypatch.setattr(ui.curses, "mousemask", mouse.append)
    monkeypatch.setattr(ui.curses, "curs_set", show_cursor)
    controller = SimpleNamespace(close=lambda: events.append("close-workers"))
    if failure:
        with pytest.raises(type(failure)) as caught:
            ui.run(controller)
        assert caught.value is failure
    else:
        ui.run(controller)
    assert events == ["repaint", "endwin", "close-workers"]
    assert mouse == [0]


def test_cleanup_error_preserves_original_failure_and_still_closes_workers(monkeypatch):
    original = RuntimeError("Original UI failure")
    closed = []
    def fail(*_):
        raise original
    def unavailable(*_):
        raise ui.curses.error("Terminal resized during cleanup")
    window = SimpleNamespace(bkgd=unavailable, clear=unavailable)
    monkeypatch.setattr(ui.TerminalApp, "loop", fail)
    monkeypatch.setattr(ui.curses, "wrapper", lambda callback: callback(window))
    monkeypatch.setattr(ui.curses, "mousemask", unavailable)
    monkeypatch.setattr(ui.curses, "curs_set", unavailable)
    with pytest.raises(RuntimeError) as caught:
        ui.run(SimpleNamespace(close=lambda: closed.append(True)))
    assert caught.value is original and closed == [True]
