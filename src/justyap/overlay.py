"""Floating "listening" island at the bottom-centre of the screen.

Runs under the system Python (PyGObject) as an X11 popup on XWayland, so it stays
above every window without ever taking keyboard focus. The daemon drives it over
stdin, one command per line: `level <0..1>`, `processing`, `done`, `error <message>`.
`done` says "Copied to clipboard", but only when the accessibility bus shows that nothing
focused takes text, so the paste had nowhere to go. EOF closes the island, after a short
pause when it is showing a message.
"""

import math
import os
import sys

import cairo
import gi

os.environ["NO_AT_BRIDGE"] = "1"  # keep the island out of the accessibility tree it queries
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

try:
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
except (ValueError, ImportError):
    Atspi = None

WIDTH, HEIGHT, BOTTOM_MARGIN = 188, 40, 56
BARS, BAR_WIDTH, BAR_GAP = 7, 3, 3
BAR_SHAPE = (0.45, 0.7, 0.9, 1.0, 0.9, 0.7, 0.45)
FRAME_MS = 33
EASING = 0.35
BACKGROUND = (0.07, 0.07, 0.08, 0.94)
ACCENT = (0.85, 0.47, 0.34)
TEXT = (0.93, 0.93, 0.95)
ERROR = (1.0, 0.45, 0.42)
DONE = (0.45, 0.85, 0.55)
MESSAGES = {"done": (DONE, 1500), "error": (ERROR, 2500)}  # colour, how long it stays (ms)
DONE_TEXT = "Copied to clipboard"
TICK_WIDTH = 18  # the tick drawn before DONE_TEXT, including the gap
A11Y_TIMEOUT_MS = 300


def can_paste() -> bool:
    """Whether the focused element takes text. Unknown counts as yes, so "Copied" never shows after a paste that worked."""
    if Atspi is None:
        return True
    Atspi.set_timeout(A11Y_TIMEOUT_MS, A11Y_TIMEOUT_MS)
    focused = Atspi.MatchRule.new(
        Atspi.StateSet.new([Atspi.StateType.FOCUSED]),
        Atspi.CollectionMatchType.ALL,
        {},
        Atspi.CollectionMatchType.ALL,
        [],
        Atspi.CollectionMatchType.ALL,
        [],
        Atspi.CollectionMatchType.ALL,
        False,
    )
    try:
        desktop = Atspi.get_desktop(0)
        for app in (desktop.get_child_at_index(i) for i in range(desktop.get_child_count())):
            for window in (app.get_child_at_index(i) for i in range(app.get_child_count())):
                if not window.get_state_set().contains(Atspi.StateType.ACTIVE):
                    continue
                collection = window.get_collection_iface()
                matches = collection.get_matches(focused, Atspi.CollectionSortOrder.CANONICAL, 1, True) if collection else []
                if not matches:
                    return True
                target = matches[0]
                return target.get_state_set().contains(Atspi.StateType.EDITABLE) or target.get_role() == Atspi.Role.TERMINAL
    except GLib.Error:
        pass
    return True


FONT_SIZE = 13
LABELS = {"listening": "Listening", "processing": "Transcribing…"}


class Island(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        visual = self.get_screen().get_rgba_visual()
        if visual:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.place(WIDTH)

        self.state = "listening"
        self.message = ""
        self.level = 0.0
        self.heights = [0.0] * BARS
        self.frame = 0
        self.pending = b""

        self.connect("draw", self.draw)
        GLib.io_add_watch(sys.stdin.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP, self.read_commands)
        GLib.timeout_add(FRAME_MS, self.tick)
        self.show_all()
        self.input_shape_combine_region(cairo.Region())

    def place(self, width: int) -> None:
        self.width = width
        display = Gdk.Display.get_default()
        area = (display.get_primary_monitor() or display.get_monitor(0)).get_geometry()
        self.set_size_request(width, HEIGHT)
        self.resize(width, HEIGHT)
        self.move(area.x + (area.width - width) // 2, area.y + area.height - HEIGHT - BOTTOM_MARGIN)

    def read_commands(self, fd: int, _condition: GLib.IOCondition) -> bool:
        data = os.read(fd, 4096)
        if not data:
            if self.state in MESSAGES:
                GLib.timeout_add(MESSAGES[self.state][1], Gtk.main_quit)
            else:
                Gtk.main_quit()
            return False
        *lines, self.pending = (self.pending + data).split(b"\n")
        for line in lines:
            command, _, value = line.decode().partition(" ")
            if command == "level":
                self.level = float(value)
            elif command in LABELS:
                self.state = command
            elif command == "done" and can_paste():
                continue
            elif command in MESSAGES:
                self.state, self.message = command, value or DONE_TEXT
                self.place(max(WIDTH, int(self.text_width(self.message) + self.tick_width()) + HEIGHT))
        return True

    @staticmethod
    def text_width(text: str) -> float:
        cr = cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1))
        Island.set_font(cr)
        return cr.text_extents(text).x_advance

    def tick_width(self) -> int:
        return TICK_WIDTH if self.state == "done" else 0

    @staticmethod
    def set_font(cr: cairo.Context) -> None:
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(FONT_SIZE)

    def tick(self) -> bool:
        self.frame += 1
        for i, shape in enumerate(BAR_SHAPE):
            if self.state == "processing":
                target = 0.25 + 0.2 * math.sin(self.frame / 4 + i * 0.9)
            else:
                target = self.level * shape * (0.75 + 0.25 * math.sin(self.frame / 3 + i * 1.7))
            self.heights[i] += (target - self.heights[i]) * EASING
        self.queue_draw()
        return True

    def draw(self, _widget: Gtk.Widget, cr: cairo.Context) -> bool:
        cr.set_operator(cairo.OPERATOR_SOURCE)
        cr.set_source_rgba(0, 0, 0, 0)
        cr.paint()
        cr.set_operator(cairo.OPERATOR_OVER)

        radius = HEIGHT / 2
        cr.new_sub_path()
        cr.arc(radius, radius, radius, math.pi / 2, 3 * math.pi / 2)
        cr.arc(self.width - radius, radius, radius, 3 * math.pi / 2, math.pi / 2)
        cr.close_path()
        cr.set_source_rgba(*BACKGROUND)
        cr.fill()
        self.set_font(cr)

        if self.state in MESSAGES:
            extents = cr.text_extents(self.message)
            left = (self.width - extents.x_advance - self.tick_width()) / 2
            cr.set_source_rgb(*MESSAGES[self.state][0])
            if self.tick_width():
                cr.set_line_width(2.2)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.move_to(left + 1, HEIGHT / 2)
                cr.line_to(left + 5, HEIGHT / 2 + 4)
                cr.line_to(left + 12, HEIGHT / 2 - 4)
                cr.stroke()
            cr.move_to(left + self.tick_width(), HEIGHT / 2 + extents.height / 2 - 1)
            cr.show_text(self.message)
            return True

        cr.set_source_rgb(*ACCENT)
        for i, height in enumerate(self.heights):
            bar = 3 + max(0.0, height) * (HEIGHT - 16)
            x = radius - 2 + i * (BAR_WIDTH + BAR_GAP)
            cr.rectangle(x, (HEIGHT - bar) / 2, BAR_WIDTH, bar)
        cr.fill()

        cr.set_source_rgb(*TEXT)
        label = LABELS[self.state]
        extents = cr.text_extents(label)
        text_left = radius - 2 + BARS * (BAR_WIDTH + BAR_GAP) + 10
        cr.move_to(text_left + (WIDTH - radius - text_left - extents.x_advance) / 2, HEIGHT / 2 + extents.height / 2 - 1)
        cr.show_text(label)
        return True


if __name__ == "__main__":
    Island()
    Gtk.main()
