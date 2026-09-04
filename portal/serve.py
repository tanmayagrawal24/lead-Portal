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

import base64
import os
import secrets
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from portal import brief, config, db, lifecycle, migrate
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

#: `user:password` for HTTP Basic auth. Optional on loopback; **required** for
#: any other bind (see `cli.cmd_serve`). §1 was written for a localhost tool
#: and §8's rows are third-party personal data — the moment the page is
#: reachable from another machine, an unauthenticated read is a data breach,
#: not a convenience (audit finding 5).
BASIC_AUTH_ENV = "PORTAL_BASIC_AUTH"


def basic_auth_from_env() -> tuple[str, str] | None:
    raw = os.environ.get(BASIC_AUTH_ENV, "")
    if not raw:
        return None
    user, sep, password = raw.partition(":")
    if not sep or not user or not password:
        raise RuntimeError(f"{BASIC_AUTH_ENV} must be 'user:password'")
    return user, password


def _credentials_ok(header: str | None, expected: tuple[str, str]) -> bool:
    if not header or not header.lower().startswith("basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:].strip()).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return False
    user, _, password = decoded.partition(":")
    return secrets.compare_digest(user, expected[0]) and secrets.compare_digest(
        password, expected[1]
    )


def cross_site(request: Request) -> bool:
    """Is this a browser request from another origin? — the CSRF check.

    The one write here is a form-encoded POST, which browsers send cross-origin
    **without** a preflight, so a page the operator visits elsewhere could
    resolve the flags that block outbound contact. Two headers a browser always
    sets on such a request and a page cannot forge: `Origin`, whose host must be
    this one, and `Sec-Fetch-Site`, which must not be `cross-site`. A non-browser
    client sends neither and is not the threat this guards against.
    """
    origin = request.headers.get("origin")
    if origin:
        own = request.headers.get("host", "")
        if urlsplit(origin).netloc.lower() != own.lower():
            return True
    return request.headers.get("sec-fetch-site", "").lower() == "cross-site"


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

    credentials = basic_auth_from_env()

    @app.middleware("http")
    async def require_auth(request: Request, call_next):
        if credentials is not None and not _credentials_ok(
            request.headers.get("authorization"), credentials
        ):
            return Response(
                "authentication required",
                status_code=401,
                headers={"WWW-Authenticate": 'Basic realm="Lead Portal"'},
            )
        return await call_next(request)

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
        if cross_site(request):
            return HTMLResponse("cross-site request refused", status_code=403)
        body = (await request.body()).decode("utf-8")
        note = parse_qs(body).get("note", [""])[0]
        lead_list = read()
        # The flag must be this company's: the URL names both, and a flag id
        # that belongs to another row must not be resolvable through this one.
        owner = lead_list.flag_owner(flag_id)
        if owner is None or owner != company_id:
            return HTMLResponse("no such flag for this company", status_code=404)
        lead_list.resolve_flag(flag_id, note.strip())
        lead = lead_list.lead(company_id)
        if lead is None:  # pragma: no cover — the flag's own company
            return HTMLResponse("", status_code=404)
        return render("_detail.html", request, lead=lead, oob=True)

    def _domain_of(lead_list: LeadList, company_id: int) -> str | None:
        row = lead_list.conn.execute(
            "SELECT domain FROM company WHERE id = ?", (company_id,)
        ).fetchone()
        return None if row is None else str(row["domain"])

    @app.post("/company/{company_id}/exclude", response_class=HTMLResponse)
    async def exclude(request: Request, company_id: int) -> HTMLResponse:
        """§9's *mark excluded (with reason)* — and its reverse. The reason is
        required on the way in (§4: never exclude silently) and the badge swaps
        out of band so the row says so at once (M7)."""
        if cross_site(request):
            return HTMLResponse("cross-site request refused", status_code=403)
        body = parse_qs((await request.body()).decode("utf-8"))
        lead_list = read()
        domain = _domain_of(lead_list, company_id)
        if domain is None:
            return HTMLResponse("no such company", status_code=404)
        try:
            if body.get("lift", [""])[0]:
                lifecycle.lift_exclusion(lead_list.conn, domain)
            else:
                lifecycle.exclude(lead_list.conn, domain, body.get("reason", [""])[0])
        except ValueError as exc:
            return HTMLResponse(str(exc), status_code=400)
        lead = lead_list.lead(company_id)
        return render("_detail.html", request, lead=lead, oob=True)

    @app.post("/company/{company_id}/outreach", response_class=HTMLResponse)
    async def outreach(request: Request, company_id: int) -> HTMLResponse:
        """§9's *log an outreach attempt*. Migration 008's trigger is the
        gate; a refusal renders as the reason, with 409, rather than as a
        stack trace (M7)."""
        if cross_site(request):
            return HTMLResponse("cross-site request refused", status_code=403)
        body = parse_qs((await request.body()).decode("utf-8"))
        lead_list = read()
        domain = _domain_of(lead_list, company_id)
        if domain is None:
            return HTMLResponse("no such company", status_code=404)
        try:
            lifecycle.log_outreach(
                lead_list.conn,
                domain,
                channel=body.get("channel", [""])[0],
                notes=body.get("notes", [""])[0],
                outcome=body.get("outcome", [""])[0] or None,
            )
        except lifecycle.OutreachBlocked as exc:
            return HTMLResponse(f'<p class="blockbox">⛔ {exc}</p>', status_code=409)
        except ValueError as exc:
            return HTMLResponse(str(exc), status_code=400)
        lead = lead_list.lead(company_id)
        return render("_detail.html", request, lead=lead, oob=True)

    @app.get("/company/{company_id}/brief.md", response_class=PlainTextResponse)
    def brief_export(company_id: int) -> Response:
        """§9's *export the research brief*. Refuses with the §8 reason as
        text: a brief that cannot state its basis is not served hollow (M7)."""
        lead_list = read()
        try:
            text = brief.render(lead_list.conn, company_id)
        except LookupError:
            return PlainTextResponse("no such company", status_code=404)
        except brief.NotScored as exc:
            return PlainTextResponse(str(exc), status_code=409)
        except (brief.MissingBasis, brief.ContactBlocked) as exc:
            return PlainTextResponse(f"⛔ {exc}", status_code=409)
        domain = _domain_of(lead_list, company_id) or str(company_id)
        return Response(
            text,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="brief-{domain}.md"'
            },
        )

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
