"""App-wide logging setup.

A timestamped stderr handler plus a rotating file handler on the root logger;
every module logs through ``logging.getLogger(__name__)``. Level comes from
``STOCKS_LOG_LEVEL``.

On the Pi, stderr goes to the journal (``journalctl -u stocks-dashboard``),
which install.sh caps at 64 MB for the whole system; the rotating file in
``STOCKS_LOG_DIR`` keeps the app's own history independent of that cap.
"""
import logging
import logging.handlers
import re
import sys
import threading

from app import config

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

# Every secret the app reads from env. None/"" entries are skipped.
_SECRET_SETTINGS = (
    "SMTP_PASSWORD", "TWILIO_AUTH_TOKEN", "ALPHA_VANTAGE_KEY", "FMP_KEY", "TELEGRAM_BOT_TOKEN",
    "ALPACA_KEY_ID", "ALPACA_SECRET_KEY",
    "OAUTH_GITHUB_CLIENT_SECRET", "OAUTH_GOOGLE_CLIENT_SECRET", "OAUTH_FACEBOOK_CLIENT_SECRET",
)
# Secret-shaped text even when it isn't one of ours: key=value credentials in
# URLs and messages, Telegram bot tokens, and bearer headers.
_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(token|password|passwd|secret|api[_-]?key|apikey|auth)=([^&\s'\"]+)"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{16,}"),
)
REDACTED = "[redacted]"


class RedactSecrets(logging.Filter):
    """Masks secrets in every record before any handler writes it — the
    message, its arguments and a formatted traceback alike."""

    def __init__(self, secrets: tuple[str, ...] | None = None):
        super().__init__()
        self.secrets = secrets

    def _known(self) -> list[str]:
        if self.secrets is not None:
            return list(self.secrets)
        return [v for name in _SECRET_SETTINGS if (v := getattr(config, name, None)) and len(v) >= 4]

    def scrub(self, text: str) -> str:
        for value in self._known():
            text = text.replace(value, REDACTED)
        text = _SECRET_PATTERNS[0].sub(lambda m: f"{m.group(1)}={REDACTED}", text)
        text = _SECRET_PATTERNS[1].sub(REDACTED, text)
        return _SECRET_PATTERNS[2].sub(lambda m: f"{m.group(1)} {REDACTED}", text)

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.scrub(record.getMessage())
        record.args = None
        if record.exc_info:
            record.exc_text = self.scrub(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None
        return True


def _install_excepthooks() -> None:
    """Route otherwise-unhandled exceptions into the log before the process dies.

    Without these, an exception escaping a non-main thread (a scheduler worker,
    say) prints to stderr and is lost with the console window.
    """
    root = logging.getLogger("app.crash")

    def sys_hook(exc_type, exc, tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        root.critical("unhandled exception", exc_info=(exc_type, exc, tb))

    def thread_hook(args):
        if issubclass(args.exc_type, SystemExit):
            return
        root.critical(
            "unhandled exception in thread %s",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


def setup_logging(level: str | None = None) -> None:
    """Configure the root logger once; safe to call repeatedly (tests re-import)."""
    root = logging.getLogger()
    resolved = (level or config.LOG_LEVEL).upper()
    root.setLevel(resolved)
    formatter = logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    if not any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
        for h in root.handlers
    ):
        handler = logging.StreamHandler()
        handler.setFormatter(formatter)
        root.addHandler(handler)

    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        try:
            config.LOG_DIR.mkdir(parents=True, exist_ok=True)
            file_handler = logging.handlers.RotatingFileHandler(
                config.LOG_DIR / "backend.log",
                maxBytes=config.LOG_MAX_BYTES,
                backupCount=config.LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError as exc:
            # A read-only or missing log dir must not stop the app from booting;
            # say so on stderr and carry on with console logging only.
            root.warning("file logging disabled (%s): %s", config.LOG_DIR, exc)

    for h in root.handlers:
        if not any(isinstance(f, RedactSecrets) for f in h.filters):
            h.addFilter(RedactSecrets())

    # Uvicorn's access log is chatty at INFO; keep it but quiet noisy libraries.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    # ...except the executor, which is the only thing that reports a job being
    # skipped for a missed deadline or a still-running previous instance.
    logging.getLogger("apscheduler.executors").setLevel(logging.INFO)

    _install_excepthooks()
