"""Tora fills in a form - every box, going to each one (ai/form_fill.py).

24 Oct 2026, the owner: "if I'm asking it to fill in things, allow it to
click different places to fill in info". The boxes are read from Windows
(never a screenshot), the writer decides what is TRUE for each - from what
they said and what HeyTora knows about them, nothing invented - and each box
gets the focus (clicked when it will not take it) and is typed into. Never a
password, card, bank or ID box; never Submit or Enter; leaving the window
stops it.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

import companion_manager as cm
from ai import form_fill, gemini, uia_page, write_for_me
from ai.form_fill import Field


class Box:
    """A text box in a fake form."""

    def __init__(self, name, rect, value="", *, password=False, read_only=False, shown=True,
                 takes_focus=True, takes_typing=True, takes_set=True, mask=None, help_text=""):
        self.Name, self.HelpText, self.AutomationId = name, help_text, ""
        self.IsPassword, self.IsOffscreen = password, not shown
        self.BoundingRectangle = SimpleNamespace(left=rect[0], top=rect[1], right=rect[2], bottom=rect[3])
        self.text, self.read_only = value, read_only
        self.takes_focus, self.takes_typing, self.takes_set, self.mask = takes_focus, takes_typing, takes_set, mask

    def GetValuePattern(self):
        return SimpleNamespace(IsReadOnly=self.read_only, Value=self.text)


class Form:
    """The window: its boxes, which one has the focus, and a keyboard into it."""

    def __init__(self, monkeypatch, boxes, *, document=True):
        self.boxes, self.focused, self.window, self.clicks, self.keys = boxes, None, ("hwnd", "Sign up - Opera"), [], []
        doc, win = SimpleNamespace(kind="document"), SimpleNamespace(kind="window")
        import uiautomation as auto
        monkeypatch.setattr(uia_page, "window", lambda: win)

        def find_all(root, *, control_type=None, limit=40, name=None):
            if control_type == auto.ControlType.DocumentControl:
                return [doc] if document else []
            return list(self.boxes)[:limit]
        monkeypatch.setattr(uia_page, "find_all", find_all)
        monkeypatch.setattr(uia_page, "value", lambda c: c.text)
        monkeypatch.setattr(uia_page, "shown", lambda c: not c.IsOffscreen)
        monkeypatch.setattr(uia_page, "has_focus", lambda c: self.focused is c)

        def focus(c, seconds=1.0):
            if c.takes_focus:
                self.focused = c
        monkeypatch.setattr(uia_page, "focus", focus)

        def set_value(c, text):
            if not c.takes_set:
                return False
            c.text = text
            return True
        monkeypatch.setattr(uia_page, "set_value", set_value)
        monkeypatch.setattr(uia_page, "wait", lambda found, seconds: found())
        monkeypatch.setattr(form_fill, "FOCUS_WAIT_S", 0)

    def click(self, x, y):
        self.clicks.append((x, y))
        for b in self.boxes:
            r = b.BoundingRectangle
            if r.left <= x <= r.right and r.top <= y <= r.bottom:
                self.focused = b

    # the keyboard
    def where(self):
        return self.window

    def select_all(self):
        self.keys.append("ctrl+a")

    def type(self, text):
        self.keys.append(text)
        box = self.focused
        if box is not None and box.takes_typing:
            box.text = box.mask(text) if box.mask else text


def _form(monkeypatch, **kw):
    boxes = [
        Box("Email", (100, 300, 400, 330)),
        Box("First name", (100, 200, 240, 230)),
        Box("Last name", (260, 202, 400, 232)),
        Box("Password", (100, 400, 400, 430), password=True),
        Box("Card number", (100, 450, 400, 480)),
        Box("Order total", (100, 500, 400, 530), value="R 120", read_only=True),
        Box("Hidden", (100, 600, 400, 630), shown=False),
        Box("", (100, 650, 102, 652)),                                     # 2 px: not a box anyone sees
    ]
    return Form(monkeypatch, boxes, **kw), boxes


# -- reading the form -------------------------------------------------------------------------------------------------

def test_the_forms_boxes_top_to_bottom_left_to_right_and_the_secret_ones_marked(monkeypatch):
    form, boxes = _form(monkeypatch)
    fields = form_fill.read_fields()
    assert [(f.key, f.label, f.secret) for f in fields] == [
        ("f1", "First name", False), ("f2", "Last name", False), ("f3", "Email", False),
        ("f4", "Password", True), ("f5", "Card number", True)]
    assert fields[0].centre == (170, 215)


def test_outside_a_page_the_browsers_own_bar_is_not_a_box(monkeypatch):
    form, _ = _form(monkeypatch, document=False)
    form.boxes.append(Box("Address and search bar", (100, 20, 900, 50)))
    assert "Address and search bar" not in [f.label for f in form_fill.read_fields()]


def test_a_box_with_no_name_is_known_by_its_hint(monkeypatch):
    form = Form(monkeypatch, [Box("", (10, 10, 200, 40), help_text="Your phone number")])
    assert form_fill.read_fields()[0].label == "Your phone number"


# -- deciding what goes in --------------------------------------------------------------------------------------------

def test_the_writer_gets_the_boxes_page_and_what_is_known_and_invents_nothing():
    fields = [Field("f1", "First name"), Field("f2", "Email", value="old@x.com"), Field("f3", "Password", secret=True)]
    text = form_fill.ask("fill it in with my details", fields, page="Create your account", known="Their name: Torben")
    assert 'f1: "First name" - empty' in text and 'f2: "Email" - holds "old@x.com"' in text
    assert 'f3: "Password" - secret, never fill' in text
    assert "Create your account" in text and "Their name: Torben" in text and "fill it in with my details" in text
    system = " ".join(form_fill._SYSTEM.split())
    assert "Never invent anything" in system and "left out" in system


def test_only_real_boxes_that_are_not_secret_get_a_value(monkeypatch):
    fields = [Field("f1", "First name"), Field("f2", "Phone"), Field("f3", "Password", secret=True)]

    async def generate_json(model, contents, schema, **kw):
        assert kw["think"] == 0 and "required" in schema
        return {"fill": [{"key": "f1", "value": " Torben "}, {"key": "f2", "value": "  "},
                         {"key": "f3", "value": "hunter2"}, {"key": "f9", "value": "x"}], "note": "No phone given."}
    monkeypatch.setattr(gemini, "generate_json", generate_json)
    values, note = asyncio.run(form_fill.plan("my details", fields))
    assert values == {"f1": "Torben"} and note == "No phone given."


# -- into the boxes ---------------------------------------------------------------------------------------------------

def test_each_box_is_gone_to_and_typed_into_never_submitted(monkeypatch):
    form, boxes = _form(monkeypatch)
    fields = form_fill.read_fields()
    seen = []
    result = form_fill.fill(fields, {"f1": "Torben", "f2": "Mikkelsen", "f3": "t@example.com"}, keyboard=form,
                            on_field=lambda f: seen.append(f.label), clicker=form.click)
    assert [b.text for b in boxes[:3]] == ["t@example.com", "Torben", "Mikkelsen"]
    assert seen == ["First name", "Last name", "Email"], "her cursor goes to each box first"
    assert result.done == [("First name", "Torben"), ("Last name", "Mikkelsen"), ("Email", "t@example.com")]
    assert form.keys.count("ctrl+a") == 3 and not any("\n" in k or "\r" in k for k in form.keys), "never Enter"
    assert boxes[3].text == "" and boxes[4].text == "", "the password and the card are never touched"


def test_a_box_that_will_not_take_the_focus_is_clicked(monkeypatch):
    form = Form(monkeypatch, [Box("Company", (100, 100, 300, 130), takes_focus=False)])
    fields = form_fill.read_fields()
    result = form_fill.fill(fields, {"f1": "Agentora"}, keyboard=form, clicker=form.click)
    assert form.clicks == [(200, 115)] and result.done == [("Company", "Agentora")]


def test_a_phone_box_that_formats_itself_still_counts(monkeypatch):
    form = Form(monkeypatch, [Box("Phone", (100, 100, 300, 130), mask=lambda t: f"({t[:3]}) {t[3:6]}-{t[6:]}")])
    result = form_fill.fill(form_fill.read_fields(), {"f1": "0821234567"}, keyboard=form, clicker=form.click)
    assert result.done == [("Phone", "0821234567")] and form.boxes[0].text == "(082) 123-4567", "its own format kept"


def test_typing_that_does_not_land_is_set_by_windows_and_a_box_that_takes_neither_is_named(monkeypatch):
    stubborn, frozen = Box("City", (100, 100, 300, 130), takes_typing=False), \
        Box("Suburb", (100, 200, 300, 230), takes_typing=False, takes_set=False)
    form = Form(monkeypatch, [stubborn, frozen])
    result = form_fill.fill(form_fill.read_fields(), {"f1": "Durban", "f2": "Umhlanga"}, keyboard=form,
                            clicker=form.click)
    assert stubborn.text == "Durban" and result.done == [("City", "Durban")] and result.failed == ["Suburb"]


def test_leaving_the_window_stops_it_where_it_is(monkeypatch):
    form, boxes = _form(monkeypatch)
    fields = form_fill.read_fields()

    def leave_after_first(f):
        if f.label == "Last name":
            form.window = ("hwnd2", "WhatsApp")
    result = form_fill.fill(fields, {"f1": "Torben", "f2": "Mikkelsen", "f3": "t@example.com"}, keyboard=form,
                            on_field=leave_after_first, clicker=form.click)
    assert result.stopped and boxes[1].text == "Torben" and boxes[0].text == "", "nothing typed into WhatsApp"


def test_a_new_line_only_goes_into_a_box_tall_enough_for_one(monkeypatch):
    short, tall = Box("Subject", (100, 100, 500, 130)), Box("Message", (100, 200, 500, 320))
    form = Form(monkeypatch, [short, tall])
    form_fill.fill(form_fill.read_fields(), {"f1": "Hello\nthere", "f2": "Hi,\n\nThanks."}, keyboard=form,
                   clicker=form.click)
    assert short.text == "Hello there", "a new line in a one-line box can send the form"
    assert tall.text == "Hi,\n\nThanks."


def test_what_she_says_names_the_boxes_not_what_went_in(monkeypatch):
    result = form_fill.Filled(done=[("Email", "t@example.com"), ("First name", "Torben")], left=["Phone"],
                              failed=["Suburb"])
    words = form_fill.summary(result, "No phone number was given.")
    assert words == ("Filled 2 boxes: Email, First name. Left empty (nothing true to put): Phone. "
                     "Would not take it: Suburb. No phone number was given.")
    assert "t@example.com" not in words


# -- the voice tool ---------------------------------------------------------------------------------------------------

def _manager(monkeypatch, *, title="Sign up - Opera", fields=None, values=None, sensitive=False):
    mgr = cm.CompanionManager.__new__(cm.CompanionManager)
    mgr._activity = lambda what: None
    mgr._privacy_guard = True
    told = {"card": [], "points": [], "held": [], "released": 0}
    mgr.sig_response_chunk = SimpleNamespace(emit=told["card"].append)
    mgr.sig_point_at = SimpleNamespace(emit=lambda x, y, label: told["points"].append((x, y, label)))
    mgr.sig_point_hold = SimpleNamespace(emit=told["held"].append)
    mgr.sig_point_release = SimpleNamespace(emit=lambda: told.__setitem__("released", told["released"] + 1))
    monkeypatch.setattr(cm, "active_window_title", lambda: title)
    monkeypatch.setattr(cm, "is_sensitive_window", lambda t: sensitive)
    fields = fields if fields is not None else [Field("f1", "First name", rect=(100, 200, 240, 230), control=object()),
                                                Field("f2", "Phone", rect=(100, 300, 240, 330), control=object())]
    monkeypatch.setattr(form_fill, "read_fields", lambda: fields)
    monkeypatch.setattr(write_for_me, "page_context", lambda: "Create your account")
    asked = {}

    async def plan(request, fs, page, known):
        asked.update(request=request, page=page, known=known)
        return (values if values is not None else {"f1": "Torben"}), "No phone number was given."
    monkeypatch.setattr(form_fill, "plan", plan)

    def fill(fs, vals, *, keyboard=None, on_field=None, clicker=None):
        for f in fs:
            if f.key in vals:
                on_field(f)
        return form_fill.Filled(done=[(f.label, vals[f.key]) for f in fs if f.key in vals],
                                left=[f.label for f in fs if f.key not in vals])
    monkeypatch.setattr(form_fill, "fill", fill)
    monkeypatch.setattr(write_for_me, "Keyboard", lambda: SimpleNamespace(modifiers_down=lambda: False))

    async def recall(query, k=5):
        return [SimpleNamespace(text="Torben's phone is 082 555 0101")]
    monkeypatch.setattr(cm.gemini_memory, "recall", recall)
    monkeypatch.setenv("TORA_USER_NAME", "Torben")
    return mgr, told, asked


def test_the_voice_tool_fills_the_form_with_her_cursor_on_each_box(monkeypatch, caplog):
    mgr, told, asked = _manager(monkeypatch)
    with caplog.at_level(logging.INFO, logger="tora.manager"):
        out = asyncio.run(mgr._tool_fill_form({"request": "fill this in with my details"}))
    assert out["ok"] and "Filled 1 box: First name." in out["result"] and "Left empty" in out["result"]
    assert "Nothing was sent" in out["result"] and "press the button themselves" in out["result"]
    assert told["points"] == [(170.0, 215.0, "First name")] and told["held"] == [True, False] and told["released"] == 1
    assert told["card"] == ["First name: Torben\n\n"]
    assert "Their name: Torben" in asked["known"] and "082 555 0101" in asked["known"], "what she knows about them"
    assert asked["page"] == "Create your account"
    assert "fill form: 2 boxes" in caplog.text and "1 filled, 1 left empty" in caplog.text


def test_a_private_window_is_never_filled(monkeypatch):
    mgr, told, asked = _manager(monkeypatch, sensitive=True)
    out = asyncio.run(mgr._tool_fill_form({"request": "fill it in"}))
    assert "private" in out["error"] and asked == {}


def test_no_boxes_says_open_the_form(monkeypatch):
    mgr, *_ = _manager(monkeypatch, fields=[Field("f1", "Password", secret=True)])
    assert "No boxes to fill in" in asyncio.run(mgr._tool_fill_form({"request": "fill it in"}))["error"]


def test_nothing_true_to_put_asks_for_the_details(monkeypatch):
    mgr, told, _ = _manager(monkeypatch, values={})
    out = asyncio.run(mgr._tool_fill_form({"request": "fill it in"}))
    assert "ask them for the details" in out["error"] and told["points"] == []


def test_the_live_model_knows_whole_forms_from_one_box():
    import config
    from ai import gemini_realtime
    decl = {d["name"]: d for d in gemini_realtime.tool_declarations()[0]["functionDeclarations"]}["fill_form"]
    assert decl["parameters"]["required"] == ["request"]
    assert "never presses Submit" in decl["description"] and "write_for_me" in decl["description"]
    rules = " ".join(config._REALTIME_RULES.split())
    assert "is fill_form, with everything they said" in rules
    assert "fill_form" in cm.CompanionManager._tool_handlers(cm.CompanionManager.__new__(cm.CompanionManager))
