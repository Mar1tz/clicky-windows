"""Packs (DESIGN.md §3G): Tora for your line of work - and every pack the same.

There are seventeen packs (Accounting, Freelancer, Lettings, Students...)
and the app used to treat one of them as the product: Accounting had a row
in the sidebar and a page, the rest a switch in a list. Now:

* **Packs** - your packs (on) with their headline numbers, then every pack
  as a card in a grid: its colour, what it does, a switch. A card opens a
  drawer with everything it adds and what to say.
* **A page per pack you turn on** - its own row in the sidebar, under
  Packs. Kit-built packs hand over a snapshot (a title, numbers, up to three
  lists) and their action buttons, and the page draws it in the app's own
  parts (ui/app_parts.py); a pack that draws its own dashboard (Accounting
  does) has it in the same page shell.

No pack is special here: none comes first, none has a page the others lack.
"""

from __future__ import annotations

import os
from typing import Optional

from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ui import app_theme as T
from ui import tokens
from ui.app_pages import Page, _ask, _clip, _tidy
from ui.app_widgets import Switch, button, icon, label

# Each pack's look: its colour family (tokens.PACK, the onboarding cards')
# and its icon. A pack not listed gets the plain one - never a crash.
LOOK = {
    "accounting": ("ledger", "calculator"), "creator": ("video", "video"),
    "developer": ("code", "code"), "freelancer": ("briefcase", "briefcase"),
    "household": ("wallet", "wallet"), "lettings": ("calendar", "key-round"),
    "marketing": ("chart", "megaphone"), "payroll": ("ledger", "banknote"),
    "podcast": ("mic", "mic"), "sales": ("chart", "handshake"),
    "store": ("briefcase", "store"), "students": ("cap", "graduation-cap"),
    "taxpractice": ("ledger", "file-text"), "teachers": ("cap", "presentation"),
    "trades": ("calendar", "wrench"),
}
PLAIN = ("chart", "package")


def look(ext_id: str) -> tuple[tuple[str, str], str]:
    family, glyph = LOOK.get(ext_id, PLAIN)
    return tokens.PACK.get(family, tokens.PACK["chart"]), glyph


def page_key(ext_id: str) -> str:
    return f"pack/{ext_id}"


class PackTile(QWidget):
    """A pack's tile: its two colours, its icon in white."""

    def __init__(self, ext_id: str, size: int = 44):
        super().__init__()
        (self._a, self._b), self._glyph = look(ext_id)
        self.setFixedSize(size, size)

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        g = QLinearGradient(r.topLeft(), r.bottomRight())
        g.setColorAt(0.0, QColor(self._a))
        g.setColorAt(1.0, QColor(self._b))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(r, r.width() * 0.28, r.width() * 0.28)
        s = int(r.width() * 0.5)
        p.drawPixmap((self.width() - s) // 2, (self.height() - s) // 2, icon(self._glyph, tokens.ON_COLOUR, s))
        p.end()


class _Card(QFrame):
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("packCard")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


# ── the Packs page ────────────────────────────────────────────────────────────

class PacksPage(Page):
    HEADING = "Packs"
    LIVE = True
    TOPICS = frozenset({"extensions"})
    COLUMNS = 3

    def __init__(self, bridge=None):
        self._errors: dict[str, str] = {}
        self._drawer: Optional[PackDrawer] = None
        super().__init__(bridge)

    def build(self) -> None:
        from ui.app_parts import empty, section_head
        br = self.bridge
        if br.workforce() is None:
            self.body.add(empty("The agents did not start",
                                "Packs plug into the agents, so none can run until they do - "
                                "see tora.log for why.", "extensions"))
            return
        packs = br.extensions()
        if not packs:
            self.body.add(empty("No packs found", "Packs live in the extensions folder next to "
                                "HeyTora.", "extensions"))
            return
        on = [p for p in packs if p.get("enabled")]
        self.body.add(section_head("Your packs"))
        if on:
            grid = QGridLayout()
            grid.setSpacing(T.GAP)
            for i, info in enumerate(on):
                grid.addWidget(self._yours(info), i // 2, i % 2)
            self.body.add_layout(grid)
        else:
            self.body.add(empty("No packs on yet", "Pick the one for your work below - "
                                "Tora gets its agents, tools and a page of its own.", "extensions"))
        self.body.add(section_head("All packs"))
        grid = QGridLayout()
        grid.setSpacing(T.GAP)
        for i, info in enumerate(packs):
            grid.addWidget(self._card(info), i // self.COLUMNS, i % self.COLUMNS)
        for c in range(self.COLUMNS):
            grid.setColumnStretch(c, 1)
        self.body.add_layout(grid)

    def _yours(self, info: dict) -> QWidget:
        """A pack you have on: its headline numbers, and the way in."""
        ext_id = info["id"]
        card = _Card()
        card.clicked.connect(lambda: self.navigate.emit(page_key(ext_id)))
        lay = QHBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(14)
        lay.addWidget(PackTile(ext_id, 44), 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(3)
        col.addWidget(label(info.get("name") or ext_id, "cardTitle"))
        view = _ask(self.bridge, "pack_view", None, ext_id)
        numbers = (view or {}).get("numbers") or []
        line = " · ".join(f"{n} {c.lower()}" for c, n in numbers[:3]) if numbers else \
            _clip(info.get("description"), 90)
        col.addWidget(label(line, "dim", wrap=True))
        lay.addLayout(col, 1)
        go = button("Open")
        go.clicked.connect(lambda: self.navigate.emit(page_key(ext_id)))
        lay.addWidget(go, 0, Qt.AlignmentFlag.AlignVCenter)
        return card

    def _card(self, info: dict) -> QWidget:
        """Every pack, one card: colour, name, what it does, a switch."""
        ext_id, on = info["id"], bool(info.get("enabled"))
        card = _Card()
        card.clicked.connect(lambda: self.open_pack(ext_id))
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.setSpacing(8)
        top = QHBoxLayout()
        top.addWidget(PackTile(ext_id, 40))
        top.addStretch(1)
        switch = Switch(on)
        switch.toggled.connect(lambda v, e=ext_id: self._turn(e, v))
        top.addWidget(switch, 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(top)
        lay.addSpacing(4)
        lay.addWidget(label(info.get("name") or ext_id, "cardTitle"))
        lay.addWidget(label(_clip(info.get("description"), 110), "rowSub", wrap=True))
        if self._errors.get(ext_id):
            why = label(f"That did not work: {self._errors[ext_id]}", wrap=True)
            why.setStyleSheet(f"color: {T.DANGER};")
            lay.addWidget(why)
        lay.addStretch(1)
        return card

    def _turn(self, ext_id: str, on: bool) -> None:
        why = self.bridge.set_extension(ext_id, on)
        if why:
            self._errors[ext_id] = why
        else:
            self._errors.pop(ext_id, None)
        # Redrawn after the click has finished: the switch being clicked is
        # one of the widgets a redraw replaces.
        QTimer.singleShot(0, self.refresh)

    def open_pack(self, ext_id: str) -> None:
        if self._drawer is None:
            self._drawer = PackDrawer(self, self.bridge, self._turn, self.navigate.emit)
        self._drawer.show_pack(ext_id)


# ── a pack's drawer ───────────────────────────────────────────────────────────

from ui.app_parts import Drawer  # noqa: E402


class PackDrawer(Drawer):
    """Everything about one pack: what it does, everything it adds, what to
    say, what it works with, what it needs, and its switch."""

    def __init__(self, page: QWidget, bridge, turn, go):
        super().__init__(page)
        self.bridge, self._turn, self._go = bridge, turn, go
        self._ext_id = ""

    def show_pack(self, ext_id: str) -> None:
        self._ext_id = ext_id
        self.open()

    def refresh(self) -> None:
        from ui.app_pages import _brand
        self.clear()
        info = next((p for p in self.bridge.extensions() if p.get("id") == self._ext_id), None)
        if info is None:
            self.body.addWidget(label("That pack is not here any more.", "dim"))
            return
        ext_id, on = info["id"], bool(info.get("enabled"))
        self.header(info.get("name") or ext_id)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(PackTile(ext_id, 52))
        top.addWidget(label("On" if on else "Off", "dim"), 1)
        switch = Switch(on)
        switch.toggled.connect(lambda v: (self._turn(ext_id, v), QTimer.singleShot(50, self.refresh)))
        top.addWidget(switch)
        holder = QWidget()
        holder.setLayout(top)
        self.body.addWidget(holder)
        if info.get("description"):
            self.body.addWidget(label(info["description"], wrap=True))
        if info.get("offer"):
            self.body.addSpacing(6)
            self.body.addWidget(label("WHAT IT ADDS", "sectionTitle"))
            adds = QWidget()
            col = QVBoxLayout(adds)
            col.setContentsMargins(0, 0, 0, 0)
            col.setSpacing(6)
            for item in info["offer"]:
                row = QHBoxLayout()
                row.setSpacing(8)
                tick = QLabel()
                tick.setPixmap(icon("check", T.OK, 14))
                row.addWidget(tick, 0, Qt.AlignmentFlag.AlignTop)
                row.addWidget(label(item, wrap=True), 1)
                col.addLayout(row)
            self.body.addWidget(adds)
        view = _ask(self.bridge, "pack_view", None, ext_id) if on else None
        said = (view or {}).get("say") or []
        phrases = info.get("activation_phrases") or []
        self.body.addSpacing(6)
        self.body.addWidget(label("WHAT TO SAY", "sectionTitle"))
        if said:
            for words in said:
                self.body.addWidget(label(f"“{words[:1].upper()}{words[1:]}”", "dim", wrap=True))
        elif phrases:
            self.body.addWidget(label(f"Turn it on here, or tell Tora “{phrases[0][:1].upper()}"
                                      f"{phrases[0][1:]}” and she will offer it.", "dim", wrap=True))
        if info.get("optional_integrations"):
            self.body.addSpacing(6)
            self.body.addWidget(label("WORKS WITH", "sectionTitle"))
            self.body.addWidget(label(", ".join(_brand(i) for i in info["optional_integrations"]),
                                      "dim", wrap=True))
        needs = [r for r in (info.get("requires") or []) if r != ext_id]
        if needs:
            names = {p["id"]: p.get("name") or p["id"] for p in self.bridge.extensions()}
            self.body.addWidget(label("Needs " + ", ".join(names.get(r, r) for r in needs) +
                                      " - it is turned on with it.", "faint", wrap=True))
        if on:
            self.body.addSpacing(10)
            go = button(f"Open {info.get('name') or ext_id}", "primary")
            go.clicked.connect(lambda: (self.close_drawer(), self._go(page_key(ext_id))))
            self.body.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        self.body.addStretch(1)


# ── one pack's page ───────────────────────────────────────────────────────────

class PackPage(Page):
    """A pack you have on: its numbers, its lists, its buttons - drawn from
    the pack's own snapshot in the app's parts - or, for a pack that draws
    its own dashboard, that dashboard in the same shell."""

    LIVE = True
    TOPICS = frozenset({"extensions", "agents"})

    def __init__(self, bridge, ext_id: str):
        self.ext_id = ext_id
        info = next((p for p in (_ask(bridge, "extensions", []) or []) if p.get("id") == ext_id), {})
        self.HEADING = info.get("name") or ext_id
        self._dash = None
        self._said = ""
        super().__init__(bridge)
        # A pack's numbers move as its agents work; read them again while shown.
        self._tick = QTimer(self)
        self._tick.setInterval(10000)
        self._tick.timeout.connect(lambda: self.isVisible() and self.refresh())
        self._tick.start()

    def build_title(self) -> QWidget:
        from ui.app_widgets import title
        row = QWidget()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        lay.addWidget(PackTile(self.ext_id, 40))
        lay.addWidget(title(self.HEADING))
        lay.addStretch(1)
        return row

    def build(self) -> None:
        from ui.app_parts import empty
        br = self.bridge
        if br.workforce() is None:
            self.body.add(empty("The agents did not start", "This pack runs on them - see "
                                "tora.log for why.", "extensions"))
            return
        if not br.extension_on(self.ext_id):
            self._drop_dashboard()
            self.body.add(empty(f"{self.HEADING} is off", "Turn it on to use it here - "
                                "its agents, its tools and this page.", "extensions"))
            row = QHBoxLayout()
            row.setSpacing(8)
            on = button(f"Turn on {self.HEADING}", "primary")
            on.clicked.connect(self._turn_on)
            row.addWidget(on)
            every = button("All packs")
            every.clicked.connect(lambda: self.navigate.emit("packs"))
            row.addWidget(every)
            row.addStretch(1)
            self.body.add_layout(row)
            if self._said:
                self.body.add(label(self._said, "faint", wrap=True))
            return
        view = br.pack_view(self.ext_id)
        if view is not None:
            self._snapshot(view)
            return
        self._own_dashboard()

    def _snapshot(self, view: dict) -> None:
        from ui.app_parts import InfoCard, ListBox, section_head, stat_tile
        br = self.bridge
        bar = QHBoxLayout()
        bar.setSpacing(8)
        for text in view["actions"]:
            b = button(text, "primary" if text == view["actions"][0] else "ghost")
            b.clicked.connect(lambda _c=False, t=text: self._act(t))
            bar.addWidget(b)
        if view["folder"]:
            folder = button("Open folder")
            folder.clicked.connect(lambda: br.open_folder(view["folder"]))
            bar.addWidget(folder)
        bar.addStretch(1)
        self.body.add_layout(bar)
        if self._said:
            self.body.add(label(self._said, "faint", wrap=True))
        if view.get("error"):
            self.body.add(label("This pack couldn't read its numbers just now - it tries again "
                                "in a moment.", "faint", wrap=True))
        if view["numbers"]:
            tiles = QHBoxLayout()
            tiles.setSpacing(T.GAP)
            for caption, value in view["numbers"][:4]:
                tiles.addWidget(stat_tile(str(value), caption), 1)
            self.body.add_layout(tiles)
        for caption, rows in view["lists"]:
            self.body.add(section_head(caption))
            if not rows:
                self.body.add(label("Nothing here yet.", "faint"))
                continue
            box = ListBox()
            for text, data in rows[:30]:
                box.add(self._list_row(text, data))
            self.body.add(box)
        if view["say"]:
            card = InfoCard("What to say")
            for words in view["say"]:
                card.body.addWidget(label(f"“{words[:1].upper()}{words[1:]}”", "dim", wrap=True))
            self.body.add(card)

    def _list_row(self, text: str, data) -> QWidget:
        from ui.app_parts import HoverRow
        row = HoverRow()
        lay = QHBoxLayout(row)
        lay.setContentsMargins(18, 12, 18, 12)
        lay.addWidget(label(text, "rowTitle", wrap=True), 1)
        if isinstance(data, str) and data and os.path.isfile(data):
            row.clicked.connect(lambda p=data: os.startfile(p))   # noqa: S606 - the user's own file
            row.setToolTip("Open it")
        else:
            row.setCursor(Qt.CursorShape.ArrowCursor)
        return row

    def _turn_on(self) -> None:
        why = self.bridge.set_extension(self.ext_id, True)
        self._said = f"That did not work: {why}" if why else ""
        QTimer.singleShot(0, self.refresh)

    def _act(self, text: str) -> None:
        ok = self.bridge.pack_action(self.ext_id, text)
        self._said = (f"“{text}” started - it shows on the Agents page, and asks before it "
                      "changes anything." if ok else f"“{text}” didn't start - see tora.log.")
        QTimer.singleShot(0, self.refresh)

    def _own_dashboard(self) -> None:
        """A pack that draws its own dashboard (Accounting's client picker,
        month-end, exceptions...), restyled to the app and kept while the
        pack stays on - it holds its own state."""
        from ui.app_widgets import Card, Row
        why = ""
        if self._dash is None:
            try:
                dash = self.bridge.dashboard(self.ext_id)
            except Exception as exc:  # noqa: BLE001 - the pack's code; the page says why
                dash, why = None, str(exc) or type(exc).__name__
            if dash is not None:
                self._dash = _tidy(dash)
                dash.setMinimumHeight(640)
        if self._dash is not None:
            self.body.add(self._dash)
            return
        card = Card()
        card.add(Row("This pack's page could not load", why or "See tora.log for why.",
                     dot=T.DANGER, first=True, wrap=True))
        self.body.add(card)

    def _drop_dashboard(self) -> None:
        """The pack went off: its dashboard goes, its clocks stopped first -
        never left alive and parentless with timers running."""
        dash, self._dash = self._dash, None
        if dash is not None:
            for clock in dash.findChildren(QTimer):
                clock.stop()
            dash.setParent(None)
            dash.deleteLater()

    def refresh(self) -> None:
        # A pack's own dashboard keeps itself up to date and holds what was
        # chosen in it: it is taken out of the body, never rebuilt.
        if self._dash is not None:
            self._dash.setParent(None)
        super().refresh()
