"""The app. A local web server on 127.0.0.1, opened in your normal browser.

No Electron, no bundler, no front-end toolchain. The interface is a handful of server
rendered pages, and a build step would buy nothing except a build step to maintain.

The bind address is the security boundary. Bound to loopback, nothing outside this machine
can reach it, which is why there is no login screen by default. Change HOST at your peril:
the moment it listens on anything else, an unauthenticated app holding a full career
history and a live API key is on the network.

    python3 main.py           or double-click run.command
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
import threading
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from config import config
from database.db import get_db, init_db
from database.models import ProfileFact
from modules.contact import bootstrap
from webapp.routes import router

log = logging.getLogger("jobapp")

@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    db = next(get_db())
    try:
        facts = db.query(ProfileFact).count()
        # imports contact details on a database that has none, and does nothing after
        bootstrap(db)
        if not facts:
            log.warning("no career facts loaded. Run: python3 scripts/seed_profile.py")
        else:
            log.info("%d career facts loaded", facts)
    finally:
        db.close()
    log.info(config.describe())
    yield


app = FastAPI(title="Job App", docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(router)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

    if config.host not in ("127.0.0.1", "localhost", "::1"):
        log.warning(
            "HOST is %s, not loopback. This app has no login by default and holds a full "
            "career history. Set HOST=127.0.0.1 unless you know exactly why not.",
            config.host,
        )

    url = f"http://{config.host}:{config.port}"
    print(f"\n  Job App is running at  {url}\n  Press Control-C to stop.\n")
    threading.Timer(1.2, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=config.host, port=config.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
