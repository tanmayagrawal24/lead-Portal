"""`portal serve` — the §9 page.

Server-rendered, HTMX for the two interactions that need one. No SPA, no build
step, no Node, no CDN: `htmx.min.js` is vendored under `portal/static/`, so the
page has no network dependency at all. §2 and §3 stand.

**What this exists for.** §5.4's safety claim reads *nothing recoverable is
discarded without a human being told* (M1.41), and the dependency was written
down with it: the claim holds only if the queue is read. Until this milestone
there was no way to read it. Five review reasons, a contact block and five
abstention rules existed only as CLI output that scrolls past.

**Three things it must not do**, each the UI form of a mistake the pipeline has
already made once:

1. *Render an abstention as a zero, or as an absent row.* A rule that declined
   to fire is the most informative thing on the page — it is the difference
   between "no blog" and "we could not tell", and it is what A7 spent four
   instances learning to say out loud.
2. *Show a contact block without explaining it.* A block a human cannot see is
   a block that gets worked around by hand, and the `outreach` trigger would
   then refuse a call the operator has already decided to make.
3. *Recompute anything.* Every number here is read; nothing is derived that the
   pipeline has not already stored. A page that scores is a second scorer.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from portal import config, db, migrate
from portal.artifacts import ArtifactStore
from portal.leadlist import Filters, LeadList, assert_evidence_reachable

HERE = Path(__file__).parent
TEMPLATES = HERE / "templates"
STATIC = HERE / "static"

#: §6.4's reasons in the operator's language. The reason code is shown next to
#: it — the code is what the database holds and what a bug report needs.
FLAG_LABELS = {
    "no_impressum": "Kein Impressum gefunden",
    "possible_marketplace_only": "Möglicherweise nur Marktplatz",
    "blog_date_unparseable": "Blogdatum nicht lesbar",
    "blog_date_unbounded": "Blogdatum ohne Obergrenze",
    "blog_cadence_unmeasurable": "Veröffentlichungsfrequenz nicht messbar",
    "blog_undetectable": "Blog nicht auffindbar",
    "catalog_not_measurable": "Katalog nicht messbar",
    "domain_moved": "Domain umgezogen",
    "duplicate_site": "Dublette",
    "fetch_persistently_failing": "Abruf schlägt dauerhaft fehl",
}

SECTION_LABELS = {
    "6.1": "Qualifikation",
    "6.2": "Chancen",
    "6.3": "Abzüge",
}


def create_app(
    db_path: Path | None = None, artifacts_root: Path | None = None
) -> FastAPI:
    """Build the app against one database.

    The connection is opened once and shared: this is a single-operator
    localhost tool (§1), SQLite handles the concurrency a browser produces, and
    a pool would be machinery for a problem that does not exist here.
    """
    path = db_path or config.db_path()
    conn = db.connect(path)
    conn.row_factory = sqlite3.Row

    version = migrate.current_version(conn)
    if version == 0:
        raise RuntimeError(f"no schema at {path} — run `portal init` first")
    # Fail at startup, not per row: a rule whose evidence cannot be traced would
    # otherwise render as a component with no link, which looks exactly like a
    # rule that legitimately reads nothing.
    assert_evidence_reachable(conn)

    store = ArtifactStore(artifacts_root or config.artifacts_root(path))
    app = FastAPI(title="Lead Portal", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES))
    templates.env.globals.update(flag_labels=FLAG_LABELS, section_labels=SECTION_LABELS)

    def read() -> LeadList:
        return LeadList(conn)

    def filters_from(request: Request) -> Filters:
        get = request.query_params.get
        return Filters(
            band=get("band", ""),
            platform=get("platform", ""),
            country=get("country", ""),
            excluded=get("excluded", ""),
            needs_review=get("needs_review", ""),
            contact_blocked=get("contact_blocked", ""),
        )

    def render(name: str, request: Request, **context) -> HTMLResponse:
        return templates.TemplateResponse(request, name, context)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        lead_list = read()
        filters = filters_from(request)
        leads = lead_list.leads(filters)
        return render(
            "index.html",
            request,
            leads=leads,
            total=len(lead_list.leads()),
            filters=filters,
            facets=lead_list.facets(),
            schema_version=version,
        )

    @app.get("/rows", response_class=HTMLResponse)
    def rows(request: Request) -> HTMLResponse:
        """The table body alone, for the filter form. The count in the header is
        swapped out of band from the same response, so the two cannot disagree."""
        lead_list = read()
        filters = filters_from(request)
        leads = lead_list.leads(filters)
        return render(
            "_rows.html",
            request,
            leads=leads,
            total=len(lead_list.leads()),
            filters=filters,
            oob=True,
        )

    @app.get("/company/{company_id}/detail", response_class=HTMLResponse)
    def detail(request: Request, company_id: int) -> HTMLResponse:
        lead = read().lead(company_id)
        if lead is None:
            return HTMLResponse(
                "<p class='muted'>Unbekannte Firma.</p>", status_code=404
            )
        return render("_detail.html", request, lead=lead)

    @app.get("/company/{company_id}/collapse", response_class=HTMLResponse)
    def collapse(company_id: int) -> HTMLResponse:
        """Closing a row empties its detail cell. No JavaScript of our own —
        htmx swaps in nothing, the cell collapses, and the button that reopens
        it is still in the summary row."""
        return HTMLResponse("")

    @app.post(
        "/company/{company_id}/flag/{flag_id}/resolve", response_class=HTMLResponse
    )
    async def resolve(request: Request, company_id: int, flag_id: int) -> HTMLResponse:
        """§9: writes `resolved_at`, `resolved_by_human = 1` and the note.

        The response re-renders the detail *and* swaps the row's state badges
        out of band, because resolving the last blocking flag lifts the contact
        block — and a block that has lifted must stop showing as one in the same
        interaction, not on the next reload.

        The body is parsed here rather than declared as `Form(...)`. Both
        FastAPI's `Form` and Starlette's `request.form()` require
        `python-multipart`, which the brief says to ask about before adding —
        and htmx posts `application/x-www-form-urlencoded`, which is four
        stdlib characters to parse and needs nothing.
        """
        body = (await request.body()).decode("utf-8")
        note = parse_qs(body).get("note", [""])[0]
        lead_list = read()
        lead_list.resolve_flag(flag_id, note.strip())
        lead = lead_list.lead(company_id)
        if lead is None:  # pragma: no cover — the flag's own company
            return HTMLResponse("", status_code=404)
        return render("_detail.html", request, lead=lead, oob=True)

    @app.get("/artifact/{artifact_id}", response_class=PlainTextResponse)
    def artifact(artifact_id: int) -> PlainTextResponse:
        """The stored bytes a signal was read off, **as text**.

        Served as `text/plain` deliberately. The operator is here to check a
        citation — does this page contain the count, the date, the markup — and
        the stored bytes are the thing that was scored. Rendering them as HTML
        would run a third party's scripts on this origin and show a page that
        may differ from what we parsed; neither serves the question being asked.
        """
        row = read().artifact(artifact_id)
        if row is None:
            return PlainTextResponse("no such artifact", status_code=404)
        header = (
            f"# artifact {row['id']} · {row['kind']} · HTTP {row['http_status']}\n"
            f"# {row['url']}\n"
            f"# fetched {row['fetched_at']}, {row['bytes'] or 0} bytes\n"
            f"# {'-' * 70}\n"
        )
        if not row["body_path"]:
            return PlainTextResponse(
                header + f"\n(no body stored: {row['error'] or 'fetch failed'})"
            )
        body = (store.root / row["body_path"]).read_bytes()
        return PlainTextResponse(header + body.decode("utf-8", errors="replace"))

    return app


def serve(host: str, port: int, db_path: Path | None = None) -> int:
    """Run the page. Localhost by default and by intent — §1 is explicit that
    this is a single-operator tool with no auth and no deployment, so binding
    anywhere reachable would be publishing an unauthenticated database."""
    import uvicorn

    uvicorn.run(create_app(db_path), host=host, port=port, log_level="info")
    return 0


__all__ = ["create_app", "serve"]
