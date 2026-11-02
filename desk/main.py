"""Entry point: `python -m desk.main`.

Loads .env, settings, logging; starts the night; runs uvicorn + 5 agents + telegram + report +
health + api price loop in ONE asyncio loop as supervised tasks (restart on crash with backoff).
First run generates APP_TOKEN and a VAPID pair into .env and prints the app URL once to stdout.
"""
from __future__ import annotations

import asyncio
import base64
import importlib
import logging
import os
import secrets
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
LOGS = ROOT / "logs"
AGENTS = ("scanner", "auditor", "analyst", "executor", "risk")
SECRET_KEYS = ("XAI_API_KEY", "TG_TOKEN", "APP_TOKEN", "VAPID_PRIVATE_KEY", "FOMO_TOKEN", "COPYBOT_TG_TOKEN")

log = logging.getLogger("desk")


# ---- .env -------------------------------------------------------------------------------
def parse_env(text: str) -> dict[str, str]:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k] = v
    return out


def load_env(path: Path = ENV_PATH) -> dict[str, str]:
    vals = parse_env(path.read_text(encoding="utf-8")) if path.exists() else {}
    for k, v in vals.items():
        os.environ.setdefault(k, v)
    return vals


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def gen_vapid() -> tuple[str, str]:
    """Raw P-256 private key (32 bytes) and uncompressed public point, both base64url."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    priv = k.private_numbers().private_value.to_bytes(32, "big")
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return _b64u(priv), _b64u(pub)


def ensure_secrets(path: Path = ENV_PATH) -> bool:
    """Generate APP_TOKEN / VAPID pair into .env if missing. Returns True if APP_TOKEN was new."""
    add: dict[str, str] = {}
    if not os.environ.get("APP_TOKEN"):
        add["APP_TOKEN"] = secrets.token_urlsafe(32)
    if not (os.environ.get("VAPID_PRIVATE_KEY") and os.environ.get("VAPID_PUBLIC_KEY")):
        add["VAPID_PRIVATE_KEY"], add["VAPID_PUBLIC_KEY"] = gen_vapid()
    if add:
        prev = path.read_text(encoding="utf-8") if path.exists() else ""
        sep = "" if not prev or prev.endswith("\n") else "\n"
        path.write_text(prev + sep + "".join(f"{k}={v}\n" for k, v in add.items()), encoding="utf-8")
        os.chmod(path, 0o600)
        os.environ.update(add)
    return "APP_TOKEN" in add


def app_url() -> str:
    base = os.environ.get("PUBLIC_URL", "").rstrip("/") or \
        f"http://{os.environ.get('HOST', '127.0.0.1')}:{os.environ.get('PORT', '8890')}"
    return base + "/"


# ---- logging ------------------------------------------------------------------------------
class Redact(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        vals = [os.environ.get(k) for k in SECRET_KEYS]
        vals = [v for v in vals if v and len(v) >= 8]
        if vals:
            msg = record.getMessage()
            for v in vals:
                msg = msg.replace(v, "***")
            record.msg, record.args = msg, None
        return True


class PerAgentFiles(logging.Handler):
    """logs/<agent>.log by logger name (last dotted part if an agent/known module), else desk.log."""
    KNOWN = set(AGENTS) | {"api", "telegram", "notify", "report", "health", "copybot"}

    def __init__(self) -> None:
        super().__init__()
        self.handlers: dict[str, TimedRotatingFileHandler] = {}
        self.fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    def emit(self, record: logging.LogRecord) -> None:
        parts = record.name.split(".")
        name = next((p for p in reversed(parts) if p in self.KNOWN), "desk")
        h = self.handlers.get(name)
        if h is None:
            h = TimedRotatingFileHandler(LOGS / f"{name}.log", when="midnight", backupCount=14, encoding="utf-8")
            h.setFormatter(self.fmt)
            self.handlers[name] = h
        h.emit(record)


def setup_logging() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    files = PerAgentFiles()
    files.addFilter(Redact())
    root.addHandler(files)
    err = logging.StreamHandler(sys.stderr)
    err.setLevel(logging.WARNING)
    err.addFilter(Redact())
    err.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(err)
    for noisy in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ---- supervisor ---------------------------------------------------------------------------
class Supervisor:
    def __init__(self) -> None:
        self.factories: dict[str, Callable[[], Awaitable[Any]]] = {}
        self.inner: dict[str, asyncio.Task] = {}
        self.started: dict[str, float] = {}
        self.outer: dict[str, asyncio.Task] = {}

    def spawn(self, name: str, factory: Callable[[], Awaitable[Any]]) -> None:
        self.factories[name] = factory
        self.outer[name] = asyncio.create_task(self._run(name), name=f"sup:{name}")

    async def _run(self, name: str) -> None:
        backoff = 1.0
        while True:
            self.started[name] = time.time()
            t0 = time.monotonic()
            inner = asyncio.create_task(self.factories[name](), name=name)
            self.inner[name] = inner
            await asyncio.wait({inner})  # own cancellation propagates, inner's does not
            if inner.cancelled():
                log.warning("%s restarted", name)
                backoff = 1.0
            else:
                exc = inner.exception()
                if exc:
                    log.error("%s crashed: %r", name, exc, exc_info=exc)
                else:
                    log.warning("%s returned, restarting", name)
                backoff = 1.0 if time.monotonic() - t0 > 300 else min(backoff * 2, 60.0)
                await asyncio.sleep(backoff)

    # interface used by report.health_check
    def available(self, name: str) -> bool:
        return name not in AGENTS or agent_available(name)

    def started_at(self, name: str) -> float | None:
        return self.started.get(name) if name in self.factories else None

    def done(self, name: str) -> bool:
        t = self.inner.get(name)
        return t is not None and t.done()

    def restart(self, name: str) -> None:
        t = self.inner.get(name)
        if t and not t.done():
            t.cancel()
        self.started[name] = time.time()


def agent_available(name: str) -> bool:
    import importlib.util
    try:
        return importlib.util.find_spec(f"desk.agents.{name}") is not None
    except ImportError:
        return False


def agent_runner(name: str) -> Callable[[], Awaitable[Any]]:
    """Imports the agent at every (re)start, so a module written later is picked up on the next try."""
    async def run() -> Any:
        if not agent_available(name):
            log.error("agent %s missing (desk/agents/%s.py), retrying in 5 min", name, name)
            await asyncio.sleep(300)
            return None
        mod = importlib.import_module(f"desk.agents.{name}")
        fn = next((getattr(mod, a) for a in ("run", "main", "loop") if callable(getattr(mod, a, None))), None)
        if fn is None:
            raise RuntimeError(f"agent {name} has no run()")
        return await fn()
    return run


async def serve() -> None:
    import uvicorn

    from . import api, config, db, notify, report, telegram
    config.load(db.settings_rows())
    night, started = report.ensure_night()
    sup = Supervisor()
    if started:
        tc = config.ticket_check(config.stack())
        msg = f"Night started, {config.get('desk.mode')} stack ${night['start_balance']:,.2f}."
        if tc["message"]:
            msg += f" Ticket check: {tc['message']}."
        log.info(msg)
        asyncio.create_task(notify.push(msg, [], kind="night"))

    host, port = os.environ.get("HOST", "127.0.0.1"), int(os.environ.get("PORT", "8890"))
    server = uvicorn.Server(uvicorn.Config(api.app, host=host, port=port, log_config=None,
                                           proxy_headers=True, access_log=False))
    sup.spawn("api", server.serve)
    for a in AGENTS:
        sup.spawn(a, agent_runner(a))
    sup.spawn("telegram", telegram.poll_loop)
    sup.spawn("report", report.report_loop)
    sup.spawn("health", lambda: report.health_loop(sup))
    sup.spawn("prices", api.price_loop)
    try:
        risk_mod = importlib.import_module("desk.agents.risk")
        if hasattr(risk_mod, "balance_loop"):
            sup.spawn("balance", risk_mod.balance_loop)
    except ImportError:
        pass
    log.info("desk up on %s:%s", host, port)
    await asyncio.gather(*sup.outer.values())


def main() -> None:
    load_env()
    new_token = ensure_secrets()
    setup_logging()
    if new_token:
        # printed ONCE, to stdout only, never to logs
        print(f"Open the desk: {app_url()}#t={os.environ['APP_TOKEN']}", flush=True)
    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
