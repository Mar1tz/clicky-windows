"""M23: no credential reaches tora.log."""
from __future__ import annotations

import logging

import logsafe

KEY = "AIzaSyFAKEfakeFAKEfakeFAKEfakeFAKEfake01"
GW = "sk-0000fake0000fake0000-fake00-000000fake"


def test_redact_covers_the_shapes_that_leaked():
    url = f"GET https://generativelanguage.googleapis.com/v1beta/models/x?key={KEY} HTTP/2 200"
    out = logsafe.redact(url)
    assert KEY not in out and "key=REDACTED" in out
    assert GW not in logsafe.redact(f"Authorization: Bearer {GW}")
    assert GW not in logsafe.redact(f"provider omniroute failed with {GW}")
    assert "AIza…REDACTED" in logsafe.redact(f"config loaded {KEY}")
    assert "gho_…REDACTED" in logsafe.redact("token gho_abcdefghijklmnopqrstuvwxyz1234")


def test_redact_leaves_ordinary_text_alone():
    line = "2026-09-22 INFO tora.manager: agent workforce ready — workspace C:/x"
    assert logsafe.redact(line) == line
    assert logsafe.redact("skew-adjusted values") == "skew-adjusted values"


def test_filter_scrubs_message_and_args(tmp_path):
    log_file = tmp_path / "t.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    logger = logging.getLogger("logsafe.test")
    logger.setLevel(logging.INFO)
    logger.handlers = [handler]
    logger.propagate = False
    handler.addFilter(logsafe.RedactingFilter())

    logger.info("request %s failed", f"https://x/y?key={KEY}")
    logger.info(f"bearer {GW} rejected")
    logger.warning("dict %(k)s", {"k": f"sk-{GW[3:]}"})
    handler.close()

    text = log_file.read_text(encoding="utf-8")
    assert KEY not in text and GW not in text
    assert "key=REDACTED" in text and "sk-…REDACTED" in text


def test_install_quiets_http_loggers_and_filters_handlers():
    root = logging.getLogger("logsafe.root.test")
    h = logging.NullHandler()
    root.handlers = [h]
    logsafe.install(root)
    logsafe.install(root)                                     # idempotent
    assert sum(isinstance(f, logsafe.RedactingFilter) for f in h.filters) == 1
    assert logging.getLogger("httpx").level >= logging.WARNING
    assert logging.getLogger("httpcore").level >= logging.WARNING


def test_scrub_existing_log_in_place(tmp_path):
    p = tmp_path / "tora.log"
    p.write_text("\n".join([
        "INFO tora: starting",
        f"INFO httpx: GET https://g/api?key={KEY} 200",
        "INFO tora: fine line",
        f"WARNING x: {GW} bad",
    ]) + "\n", encoding="utf-8")
    changed = logsafe.scrub_file(p)
    assert changed == 2
    text = p.read_text(encoding="utf-8")
    assert KEY not in text and GW not in text
    assert "starting" in text and "fine line" in text
    assert logsafe.scrub_file(p) == 0                          # nothing left to do
    assert not (tmp_path / "tora.log.tmp").exists()


def test_scrub_logs_walks_the_rotation(tmp_path):
    for name in ("tora.log", "tora.log.1", "tora.log.2"):
        (tmp_path / name).write_text(f"key={KEY}\n", encoding="utf-8")
    assert logsafe.scrub_logs(tmp_path) == 3


def test_main_installs_logsafe():
    import inspect

    import main
    src = inspect.getsource(main._setup_logging)
    assert "logsafe.install" in src and "logsafe.scrub_logs" in src


# -- FS-10: it never ran -----------------------------------------------------------------------------------------

def test_scrub_works_while_the_log_is_open(tmp_path):
    """Windows will not replace a file another handle has open. The scrub ran
    after HeyTora's own handler opened tora.log, failed, and the failure
    skipped the filter too: 256 key lines stayed, and new ones went in."""
    import logging
    p = tmp_path / "tora.log"
    p.write_text(f"INFO httpx: GET https://g/api?key={KEY} 200\nINFO tora: fine\n", encoding="utf-8")
    (tmp_path / "tora.log.tmp").write_text("a half-finished scrub", encoding="utf-8")
    holder = logging.FileHandler(p, encoding="utf-8")           # held open, as the app's handler is
    try:
        assert logsafe.scrub_file(p) == 1
    finally:
        holder.close()
    text = p.read_text(encoding="utf-8")
    assert KEY not in text and "fine" in text
    assert not (tmp_path / "tora.log.tmp").exists(), "no leftover"


def test_startup_scrubs_then_filters(tmp_path, monkeypatch):
    import logging

    import config
    import main
    (tmp_path / "tora.log").write_text(f"INFO httpx: GET https://g/api?key={KEY} 200\n", encoding="utf-8")
    monkeypatch.setattr(config, "data_dir", lambda: tmp_path)
    root = logging.getLogger()
    before = list(root.handlers)
    levels = {n: logging.getLogger(n).level for n in logsafe.NOISY_HTTP_LOGGERS}
    try:
        main._setup_logging()
        added = [h for h in root.handlers if h not in before]
        assert added and all(any(isinstance(f, logsafe.RedactingFilter) for f in h.filters) for h in added)
        assert logging.getLogger("httpx").level >= logging.WARNING
        assert logging.getLogger("primp").level >= logging.WARNING, "the search's URLs are not logged"
        logging.getLogger("tora").warning("leak? %s", f"https://g/api?key={KEY}")
        for h in added:
            h.flush()
        text = (tmp_path / "tora.log").read_text(encoding="utf-8")
        assert KEY not in text and "HeyTora starting" in text
    finally:
        for h in [h for h in root.handlers if h not in before]:
            root.removeHandler(h)
            h.close()
        for n, lvl in levels.items():
            logging.getLogger(n).setLevel(lvl)
