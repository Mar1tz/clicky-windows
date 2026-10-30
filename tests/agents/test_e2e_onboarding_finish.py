"""The first launch's end, phase E (29 Oct 2026): "there's one more thing" -
jobs she makes ready from the interview, waiting on Home as "Ready to
start" cards - then her voice and the plans; and the tour taking the old
wizard's place: the gate signs in only, the tour opens out of the pill once
the app is up (docs/ui/HEYCLICKY_ONBOARDING.md).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402

pytest.importorskip("PyQt6")

from tests.agents.test_e2e_first_launch import qapp  # noqa: E402,F401 - fixture
from tests.agents.test_e2e_onboarding_steps import FakeVoice, _run_timers, _to_step  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


# ── the jobs, kept ─────────────────────────────────────────────────────────

def test_ready_jobs_are_kept_cleaned_and_taken():
    from ui import ready_jobs as R
    jobs = R.clean([{"title": "  Draft an  outreach playbook ", "why": "You sell.", "ask": "Draft it"},
                    {"title": ""}, "nonsense", {"title": "Tidy downloads"},
                    {"title": "Three"}, {"title": "Four"}])
    assert [j.title for j in jobs] == ["Draft an outreach playbook", "Tidy downloads", "Three"]
    assert jobs[1].ask == "Tidy downloads", "no ask: its title is what she is asked"
    R.save(jobs)
    assert [j.title for j in R.load()] == [j.title for j in jobs]
    assert R.take(0).ask == "Draft it" and len(R.load()) == 2
    assert R.take(9) is None


def test_a_broken_file_is_no_jobs():
    from ui import ready_jobs as R
    R._path().write_text("{not json", encoding="utf-8")
    assert R.load() == []


def test_with_no_voice_they_come_from_the_starter_skills():
    from ui import ready_jobs as R
    from ui.setup_wizard import SKILL_CARDS
    jobs = R.from_skills(["Accounting"])
    assert jobs and jobs[0].title == SKILL_CARDS["send-an-invoice"][1]
    assert R.from_skills([]), "no answers: still something to do"


def test_her_tool_hands_them_over():
    from ui.onboarding_interview import Interview
    events = []
    iv = Interview(lambda kind, value: events.append((kind, value)))
    out = asyncio.run(iv._ready_jobs({"jobs": [{"title": "Find a lighter editing workflow",
                                                "why": "You said editing eats your evenings.",
                                                "ask": "Find me a lighter editing workflow"}]}))
    assert out == {"ok": True, "ready": 1}
    kind, jobs = events[0]
    assert kind == "jobs" and jobs[0].title == "Find a lighter editing workflow"


# ── there's one more thing… ────────────────────────────────────────────────

@pytest.fixture
def panel(qapp, monkeypatch):
    import hotkey
    import ui.onboarding_parts as OP
    import ui.onboarding_shape as O
    import ui.onboarding_steps as St
    monkeypatch.setattr(O, "animations_on", lambda: False)
    monkeypatch.setattr(OP, "play_voice", lambda name: True)
    monkeypatch.setattr(St.MicStep, "microphones", staticmethod(lambda: []))
    monkeypatch.setattr(St, "Interview", FakeVoice)
    monkeypatch.setattr(St.DriveStep, "MOVE_MOUSE", False)
    monkeypatch.setattr(hotkey.DoubleTap, "start", lambda self: None)
    monkeypatch.setattr(St, "_plans_page", lambda bridge: St.label("plans", "obNote"))
    p = O.OnboardingPanel(St.steps())
    yield p
    from tests.agents.test_e2e_onboarding_steps import throw_away
    throw_away(p)


def test_show_me_and_she_makes_them(panel, qapp):
    import ui.onboarding_steps as St
    from ui import ready_jobs as R
    step = _to_step(panel, St.OneMoreStep)
    assert not panel.go.isEnabled()
    step.show_me.click()
    assert "one more thing" in panel.voice.calls
    panel.voice.say("jobs", R.clean([{"title": "Draft an outreach playbook", "why": "You sell."},
                                     {"title": "Plan the week"}]))
    _run_timers(qapp, 700)
    assert panel.go.isEnabled() and len(step.jobs) == 2
    assert [j.title for j in R.load()] == ["Draft an outreach playbook", "Plan the week"], "kept for Home"
    assert "Home" in step.status.text()


def test_if_she_only_talks_the_starter_skills_fill_in(panel, qapp):
    import ui.onboarding_steps as St
    step = _to_step(panel, St.OneMoreStep)
    step.show_me.click()
    panel.voice.say("tora_end", "Here are a few things I could do.")
    _run_timers(qapp, 1800)
    assert step.jobs and panel.go.isEnabled(), "always something to do"


def test_offline_the_starter_skills_fill_in(panel):
    import ui.onboarding_steps as St
    step = _to_step(panel, St.OneMoreStep)
    step.show_me.click()
    panel.voice.say("state", "offline")
    assert step.jobs and panel.go.isEnabled()


def test_the_tour_ends_with_the_plans(panel):
    import ui.onboarding_steps as St
    assert St.steps()[-2:] == [St.OneMoreStep, St.PlansStep]
    step = _to_step(panel, St.PlansStep)
    assert panel.go.isEnabled() and panel.go.text() == "Start using HeyTora"
    assert step.page is not None and step.WIDE


def test_the_plans_are_the_settings_cards_without_their_title(qapp):
    import ui.onboarding_steps as St
    page = St._plans_page(None)
    assert type(page).__mro__[1].__name__ == "PlansTab"
    page.deleteLater()


# ── Home ───────────────────────────────────────────────────────────────────

class _Bridge:
    def __init__(self):
        from PyQt6.QtCore import QObject, pyqtSignal

        class Sig(QObject):
            changed = pyqtSignal(str)
        self._sig = Sig()
        self.changed = self._sig.changed
        self.started, self.away = [], []

    def ready_jobs(self):
        return [{"title": "Draft an outreach playbook", "why": "You sell."}]

    def start_ready_job(self, i):
        self.started.append(i)

    def put_away_ready_job(self, i):
        self.away.append(i)


def test_home_shows_the_ready_jobs_and_starts_one(qapp):
    from PyQt6.QtWidgets import QLabel, QPushButton
    from ui.shape_pages import HomeTab
    br = _Bridge()
    home = HomeTab(br)
    words = [w.text() for w in home.findChildren(QLabel)]
    assert "READY TO START" in words and "Draft an outreach playbook" in words
    start = next(b for b in home.findChildren(QPushButton) if b.text() == "Start")
    start.click()
    next(b for b in home.findChildren(QPushButton) if b.text() == "Not now").click()
    assert br.started == [0] and br.away == [0]
    assert "ready" in HomeTab.TOPICS, "redrawn when one is started"


def test_starting_one_asks_her_in_its_words(monkeypatch):
    from ui import ready_jobs as R
    from ui.app_bridge import AppBridge
    R.save(R.clean([{"title": "Plan the week", "ask": "Plan my week for me"}]))
    br = AppBridge()
    asked = []
    monkeypatch.setattr(br, "ask", asked.append)
    assert br.ready_jobs() == [{"title": "Plan the week", "why": ""}]
    br.start_ready_job(0)
    assert asked == ["Plan my week for me"] and R.load() == []


# ── the tour in the old wizard's place ─────────────────────────────────────

def test_main_opens_the_tour_after_the_app_is_up():
    src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert src.index("first_launch = not setup_already_ran()") < src.index("door = sign_in_gate.gate()")
    assert "OnboardingPanel(steps())" in src and "tour.attach(live)" in src
    assert "tray.on_run_setup.connect(_open_tour)" in src, "Run again is the tour too"
    assert "bridge=AppBridge(manager, tray, panel)" in src
    gate = (ROOT / "ui/sign_in_gate.py").read_text(encoding="utf-8")
    assert "(wizard_factory or SetupWizard)()" in gate, "the gate's window signs in, nothing more"
    from ui import setup_wizard
    assert not hasattr(setup_wizard, "PAGES"), "the old tour's pages are gone"


def test_dev_can_sign_in_without_the_website():
    """29 Oct: the window's only door is the website, which doesn't run
    against the local database - so tools/dev.py signs in itself."""
    src = (ROOT / "tools/dev.py").read_text(encoding="utf-8")
    assert '"signin"' in src and "/api/v1/messages" in src and "account().verify(email, code)" in src
    assert 'os.environ["TORA_DATA_DIR"] = str(DATA_DIR)' in src, "the dev data folder, never the installed app's"


def test_the_installer_carries_the_logos_and_the_tours_recordings():
    """30 Oct: assets/logos (Settings > Integrations) was missing from the
    build - the installed app would have shown no logos."""
    spec = (ROOT / "heytora.spec").read_text(encoding="utf-8")
    assert 'os.path.join("assets", "logos")' in spec
    assert 'os.path.join("assets", "onboarding")' in spec


def test_a_recording_becomes_a_clip_the_tour_plays(tmp_path, qapp):
    from PIL import Image
    from ui.onboarding_steps import Clip
    frames = [Image.new("RGB", (80, 40), (i * 60, 0, 0)) for i in range(3)]
    path = tmp_path / "circle.webp"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    clip = Clip(path, width=200)
    assert clip.movie.frameCount() == 3 and clip.view.width() == 200
    clip.start()
    clip.stop()


def test_the_tours_plans_are_the_cards_and_their_note_only(qapp):
    from PyQt6.QtWidgets import QLabel
    import ui.onboarding_steps as St
    from ui.settings_tabs import PLANS_NOTE
    page = St._plans_page(None)
    words = [w.text() for w in page.findChildren(QLabel)]
    assert PLANS_NOTE in words
    assert not any(w.strip().upper() == "BILLING" for w in words), "billing stays in Settings"
    page.deleteLater()
