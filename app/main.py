"""Web routes. Server-rendered pages with plain HTML forms (POST, then redirect)."""

from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from urllib.parse import urlencode, urlsplit
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import catalog, db, generator, inventory as inv, photos, service
from .recovery import TARGET_MUSCLES

HERE = Path(__file__).parent
app = FastAPI(title="Rackmount", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")


def fmt_weight(w) -> str:
    if w is None:
        return ""
    return f"{w:g}"


def local_time(iso: str | None, fmt: str = "%a %b %d, %I:%M %p") -> str:
    if not iso:
        return ""
    return datetime.fromisoformat(iso).astimezone().strftime(fmt)


def fmt_set(s) -> str:
    """'8·BW' / '10·45' for the PREV column."""
    reps, weight = s
    return f"{reps}·{fmt_weight(weight) if weight else 'BW'}"


def _asset_versions() -> dict[str, str]:
    """Content hash per static file. Changes on every deploy that edits a file, so phones
    fetch the new CSS/JS instead of a stale cached copy."""
    return {p.name: hashlib.sha1(p.read_bytes()).hexdigest()[:10]
            for p in (HERE / "static").iterdir() if p.is_file()}


ASSET_VERSIONS = _asset_versions()


def asset(name: str) -> str:
    return f"/static/{name}?v={ASSET_VERSIONS.get(name, '0')}"


templates.env.globals["asset"] = asset


UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _allowed_hosts() -> set[str] | None:
    """FP_ALLOWED_HOSTS (comma-separated names/IPs, set by deploy/install.sh). Unset = any host."""
    raw = os.environ.get("FP_ALLOWED_HOSTS", "").strip()
    return {h.strip().lower() for h in raw.split(",") if h.strip()} or None


def _hostname(host_header: str) -> str:
    host = host_header.strip().lower()
    if host.startswith("["):                      # [ipv6]:port
        return host[1:host.find("]")] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


@app.middleware("http")
async def guard_requests(request: Request, call_next):
    """No login by design, so block the two ways a *web page* could act on the app for you:
    - DNS rebinding: only answer to the host names/IPs the app is actually reached by.
    - Cross-site form posts: a change must come from the app's own pages (same Origin)."""
    host = request.headers.get("host", "")
    allowed = _allowed_hosts()
    if allowed is not None and _hostname(host) not in allowed:
        return PlainTextResponse("Unknown host name for this app.", status_code=400)
    if request.method in UNSAFE_METHODS:
        source = request.headers.get("origin") or request.headers.get("referer")
        if source and source != "null":
            if urlsplit(source).netloc.lower() != host.lower():
                return PlainTextResponse("Blocked: this change didn't come from the app's own pages.",
                                         status_code=403)
        elif source == "null":
            return PlainTextResponse("Blocked: this change didn't come from the app's own pages.", status_code=403)
    return await call_next(request)


@app.middleware("http")
async def no_stale_pages(request: Request, call_next):
    """Pages are always revalidated; versioned static files can be cached for a long time."""
    response = await call_next(request)
    if response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache"
    elif request.url.path.startswith("/static/") and request.query_params.get("v"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


templates.env.filters["w"] = fmt_weight
templates.env.filters["setfmt"] = fmt_set
templates.env.globals["goal_codes"] = {"strength": "STR", "hypertrophy": "HYP", "endurance": "END"}
templates.env.filters["local"] = local_time
EQUIPMENT_LABELS = {"body only": "Bodyweight", "e-z curl bar": "EZ bar", "kettlebells": "Kettlebell",
                    "plates": "Weight plate", "trap bar": "Trap bar"}


def equipment_label(exercise: dict) -> str:
    """What an exercise is done with, for lists: 'Dumbbell', 'Smith machine', 'Medicine ball'..."""
    tag = exercise["equipment"]
    named = inv.MACHINES if tag == "machine" else inv.GEAR if tag == "gear" else None
    if named:
        return next((named[r][0] for r in exercise["requires"] if r in named), tag.capitalize())
    return EQUIPMENT_LABELS.get(tag, tag.capitalize())


templates.env.filters["gear"] = equipment_label


@contextmanager
def tx():
    conn = db.connect()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def render(request: Request, name: str, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"goals": generator.GOALS, **ctx})


# ---- current profile (remembered per device in a cookie) -------------------------

PROFILE_COOKIE = "fp_profile"
COOKIE_MAX_AGE = 5 * 365 * 24 * 3600


def current_profile(request: Request, conn):
    return service.get_profile(conn, request.cookies.get(PROFILE_COOKIE))


def use_profile(response, pid: int):
    response.set_cookie(PROFILE_COOKIE, str(pid), max_age=COOKIE_MAX_AGE, httponly=True, samesite="lax")
    return response


def pick_profile() -> RedirectResponse:
    """Sent when this device hasn't chosen a profile (or its profile was deleted)."""
    return redirect("/profiles")


# ---- profiles ----------------------------------------------------------------

@app.get("/profiles", response_class=HTMLResponse)
def profiles_page(request: Request):
    with tx() as conn:
        profiles = service.list_profiles(conn)
    return render(request, "profiles.html", profiles=profiles, show_nav=False)


@app.get("/profiles/new", response_class=HTMLResponse)
def new_profile_page(request: Request):
    with tx() as conn:
        has_profiles = bool(service.list_profiles(conn))
    return render(request, "profile_new.html", form={"name": "", "goal": "hypertrophy", "n_exercises": 5},
                  error=None, preview_color=None, has_profiles=has_profiles, show_nav=False)


@app.post("/profiles", response_class=HTMLResponse)
def create_profile(request: Request, name: str = Form(""), goal: str = Form("hypertrophy"),
                   n_exercises: str = Form("5")):
    with tx() as conn:
        try:
            pid = service.create_profile(conn, name, goal, n_exercises)
        except service.ProfileError as err:
            return render(request, "profile_new.html", form={"name": name, "goal": goal, "n_exercises": n_exercises},
                          error=str(err), has_profiles=bool(service.list_profiles(conn)), show_nav=False)
    return use_profile(redirect("/"), pid)


@app.post("/profiles/{pid}/use")
def choose_profile(pid: int):
    with tx() as conn:
        if not service.get_profile(conn, pid):
            return pick_profile()
    return use_profile(redirect("/"), pid)


@app.post("/profile/rename")
def rename_profile(request: Request, name: str = Form("")):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        try:
            service.rename_profile(conn, p["id"], name)
        except service.ProfileError as err:
            return redirect("/settings?" + urlencode({"profile_error": str(err)}))
    return redirect("/settings?renamed=1")


@app.post("/profile/delete")
def delete_profile(request: Request, confirm: str = Form("")):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        if " ".join(confirm.split()).lower() != p["name"].lower():
            return redirect("/settings?" + urlencode({"delete_error": "1"}) + "#delete")
        service.delete_profile(conn, p["id"])
    response = pick_profile()
    response.delete_cookie(PROFILE_COOKIE)
    return response


# ---- today -------------------------------------------------------------------

def _open_card(request: Request, items: list[dict]) -> int | None:
    """The card to show open: one just swapped in (?open=<id>), else the next one to do."""
    wanted = request.query_params.get("open", "")
    if wanted.isdigit() and any(it["we"]["id"] == int(wanted) and not it["sets"] for it in items):
        return int(wanted)
    return service.active_exercise_id(items)


@app.get("/", response_class=HTMLResponse)
def today(request: Request):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        pid = p["id"]
        s = service.settings(conn, pid)
        w = service.current_workout(conn, pid)
        rec = service.current_recovery(conn, pid)
        ctx = {"s": s, "workout": w, "recovery": rec, "profile": p}
        if w:
            items = service.workout_detail(conn, pid, w["id"])
            targets = []
            for it in items:
                for m in it["exercise"]["primary"]:
                    if m not in targets:
                        targets.append(m)
            ctx.update(
                items=items,
                active_id=_open_card(request, items),
                pattern_label=generator.PATTERNS[w["pattern"]][0] if w["pattern"] in generator.PATTERNS else None,
                targets=[(m, rec.get(m, 100)) for m in targets[:3]],
                has_sets=service.workout_has_sets(conn, w["id"]),
                last_set=service.last_set_time(conn, w["id"]),
                rest=generator.GOALS[w["goal"]].rest_seconds,
                add_muscle=next(iter(service.day_muscles(conn, pid, w["id"])), None),
            )
        else:
            ctx["pool_size"] = len(service._pool(conn, pid, s))
            ctx["ready"] = sorted(((m, rec[m]) for m in TARGET_MUSCLES), key=lambda x: -x[1])
    return render(request, "today.html", **ctx)


@app.post("/workout/new")
def new_workout(request: Request):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        if not service.current_workout(conn, p["id"]):
            service.create_workout(conn, p["id"])
    return redirect("/")


@app.post("/workout/{wid}/regenerate")
def regenerate(request: Request, wid: int):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        service.new_exercises(conn, p["id"], wid)
    return redirect("/")


@app.post("/workout/{wid}/suggest")
def suggest_exercise(request: Request, wid: int):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        new_id = service.suggest_addition(conn, p["id"], wid)
    return redirect(f"/?open={new_id}#ex-{new_id}" if new_id else "/")


@app.get("/workout/{wid}/add", response_class=HTMLResponse)
def add_exercise_page(request: Request, wid: int, muscle: str = "", q: str = ""):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        w = service.owned_workout(conn, p["id"], wid)
        if not w or w["finished_at"]:
            return redirect("/")
        data = service.picker(conn, p["id"], wid, muscle or None, q[:60])
        label = generator.PATTERNS[w["pattern"]][0] if w["pattern"] in generator.PATTERNS else "Today"
    return render(request, "add_exercise.html", workout=w, day_label=label, **data)


@app.post("/workout/{wid}/add")
def add_exercise(request: Request, wid: int, exercise_id: str = Form("")):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        new_id = service.add_chosen(conn, p["id"], wid, exercise_id)
    return redirect(f"/?open={new_id}#ex-{new_id}" if new_id else "/")


@app.post("/workout/{wid}/discard")
def discard(request: Request, wid: int):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        conn.execute("DELETE FROM workouts WHERE id = ? AND profile_id = ? AND finished_at IS NULL", (wid, p["id"]))
    return redirect("/")


@app.post("/workout/{wid}/finish")
def finish(request: Request, wid: int):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        if not service.owned_workout(conn, p["id"], wid):
            return redirect("/")
        if service.workout_has_sets(conn, wid):
            conn.execute("UPDATE workouts SET finished_at = ? WHERE id = ?", (service.now().isoformat(), wid))
        else:
            conn.execute("DELETE FROM workouts WHERE id = ?", (wid,))
    return redirect("/history")


@app.post("/we/{we_id}/set")
def log_set(request: Request, we_id: int, reps: int = Form(...), weight: str = Form("")):
    try:
        weight_val = float(weight) if weight.strip() else None
    except ValueError:
        weight_val = None
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        if service.owned_we(conn, p["id"], we_id):
            conn.execute(
                "INSERT INTO sets(workout_exercise_id, reps, weight, logged_at) VALUES (?, ?, ?, ?)",
                (we_id, max(0, reps), weight_val, service.now().isoformat()),
            )
    return redirect(f"/#ex-{we_id}")


@app.post("/set/{set_id}/delete")
def delete_set(request: Request, set_id: int):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        row = service.owned_set(conn, p["id"], set_id)
        if row:
            conn.execute("DELETE FROM sets WHERE id = ?", (set_id,))
    return redirect(f"/#ex-{row['workout_exercise_id']}" if row else "/")


@app.post("/we/{we_id}/pin")
def pin(request: Request, we_id: int):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        row = service.owned_we(conn, p["id"], we_id)
        if row:
            service.toggle_pin(conn, p["id"], row["exercise_id"])
    return redirect(f"/#ex-{we_id}")


@app.post("/pinned/{exercise_id}/remove")
def unpin(request: Request, exercise_id: str):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        conn.execute("DELETE FROM pinned WHERE profile_id = ? AND exercise_id = ?", (p["id"], exercise_id))
    return redirect("/settings#pinned")


@app.post("/we/{we_id}/swap")
def swap(request: Request, we_id: int):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        new_id = service.swap_exercise(conn, p["id"], we_id)
    return redirect(f"/?open={new_id}#ex-{new_id}" if new_id else f"/#ex-{we_id}")


@app.post("/we/{we_id}/remove")
def remove(request: Request, we_id: int):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        service.remove_exercise(conn, p["id"], we_id)
    return redirect("/")


@app.post("/we/{we_id}/exclude")
def exclude(request: Request, we_id: int):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        service.exclude_and_replace(conn, p["id"], we_id)
    return redirect("/")


# ---- history & recovery -------------------------------------------------------

@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        s = service.settings(conn, p["id"])
        workouts = conn.execute(
            "SELECT * FROM workouts WHERE profile_id = ? AND finished_at IS NOT NULL ORDER BY id DESC LIMIT 50",
            (p["id"],)).fetchall()
        entries = [{"w": w, "lines": service.workout_detail(conn, p["id"], w["id"])} for w in workouts]
    return render(request, "history.html", s=s, entries=entries, profile=p)


@app.get("/recovery", response_class=HTMLResponse)
def recovery_page(request: Request):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        rec = service.current_recovery(conn, p["id"])
    rows = sorted(((m, rec[m]) for m in TARGET_MUSCLES), key=lambda x: (-x[1], x[0]))
    return render(request, "recovery.html", rows=rows, profile=p)


# ---- settings -------------------------------------------------------------------

def _equipment_inputs(s: dict, raw: dict | None = None) -> dict:
    """Values for the numbers-only equipment inputs (ranges, kettlebell chips, plate pairs)."""
    invent, units = s["inventory"], s["units"]
    ranges = {}
    for kind, mode in inv.LIST_INPUT.items():
        if mode != "range":
            continue
        if raw and kind in raw:
            ranges[kind] = {**raw[kind], "irregular": False}
            continue
        text = invent[kind].get("weights", "")
        r = inv.as_range(text)
        if r:
            ranges[kind] = {"from": fmt_weight(r[0]), "to": fmt_weight(r[1]) if r[2] else "",
                            "step": fmt_weight(r[2]) if r[2] else "", "irregular": False}
        else:
            try:
                ws = inv.parse_weight_list(text)
            except inv.WeightSyntaxError:
                ws = []
            ranges[kind] = {"from": fmt_weight(ws[0]) if ws else "", "to": fmt_weight(ws[-1]) if ws else "",
                            "step": "", "irregular": bool(ws)}
    try:
        kb_saved = set(inv.parse_weight_list(invent["kettlebell"].get("weights", "")))
    except inv.WeightSyntaxError:
        kb_saved = set()
    pairs = inv.plate_pairs(invent.get("plates", ""))
    plate_sizes = sorted(set(inv.PLATE_SIZES[units]) | set(pairs), reverse=True)
    return {
        "list_input": inv.LIST_INPUT,
        "ranges": ranges,
        "kb_saved": kb_saved,
        "kb_sizes": sorted(set(inv.KETTLEBELL_SIZES[units]) | kb_saved),
        "plate_rows": [(w, pairs.get(w, 0)) for w in plate_sizes],
    }


def _settings_ctx(conn, p, s, saved=False, notice=None):
    excl = sorted((catalog.get(i) for i in service.excluded_ids(conn, p["id"])), key=lambda e: e["name"])
    pins = sorted((catalog.get(i) for i in service.pinned_ids(conn, p["id"])), key=lambda e: e["name"])
    return {"s": s, "profile": p, "excluded": excl, "pinned": pins, "saved": saved, "notice": notice or {}}


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0, renamed: int = 0, profile_error: str = "",
                  delete_error: int = 0):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        notice = {"renamed": bool(renamed), "profile_error": profile_error[:200], "delete_error": bool(delete_error)}
        ctx = _settings_ctx(conn, p, service.settings(conn, p["id"]), saved=bool(saved), notice=notice)
    return render(request, "settings.html", **ctx)


@app.post("/settings")
async def save_settings(request: Request):
    """Personal settings only: goal, workout length, units. Equipment is saved on /gym."""
    form = await request.form()
    goal = form.get("goal") if form.get("goal") in generator.GOALS else "hypertrophy"
    try:
        n = min(10, max(2, int(form.get("n_exercises", 5))))
    except ValueError:
        n = 5
    units = "kg" if form.get("units") == "kg" else "lb"
    with tx() as conn:
        prof = current_profile(request, conn)
        if not prof:
            return pick_profile()
        service.save_personal_settings(conn, prof["id"], goal, n, units)
    return redirect("/settings?saved=1")


# ---- gym (shared equipment) ---------------------------------------------------------

def _gym_ctx(conn, p, s, errors=None, saved=False, raw=None):
    preview = {}
    for kind in inv.EQUIPMENT_KINDS:
        try:
            ws = inv.loadable_weights(s["inventory"], kind)
        except inv.WeightSyntaxError:
            ws = None
        if ws:
            preview[kind] = ws
    return {"s": s, "profile": p, "kinds": inv.EQUIPMENT_KINDS, "stations": inv.STATIONS, "pulleys": inv.PULLEYS,
            "machines": inv.MACHINES, "gear": inv.GEAR, "machine_step": inv.MACHINE_STEP[s["units"]],
            "preview": preview, "errors": errors or {}, "saved": saved,
            "pool_size": len(service._pool(conn, p["id"], s)) if not errors else None,
            **_equipment_inputs(s, raw)}


@app.get("/gym", response_class=HTMLResponse)
def gym_page(request: Request, saved: int = 0):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        ctx = _gym_ctx(conn, p, service.settings(conn, p["id"]), saved=bool(saved))
    return render(request, "gym.html", **ctx)


def _parse_inventory(form) -> tuple[dict, dict, dict]:
    """(inventory, raw range inputs to redisplay, errors) from the Gym form."""
    errors: dict[str, str] = {}

    def num(key: str) -> float | None:
        v = str(form.get(key, "")).strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            raise inv.WeightSyntaxError("Numbers only") from None

    inventory = {
        "stations": [k for k in inv.STATIONS if form.get(f"station_{k}")],
        "machines": [k for k in inv.MACHINES if form.get(f"machine_{k}")],
        "gear": [k for k in inv.GEAR if form.get(f"gear_{k}")],
    }
    raw: dict[str, dict] = {}
    # plates: one "pairs owned" box per size (legacy free-text "plates" field still accepted)
    plate_keys = [k for k in form.keys() if k.startswith("plate_")]
    if plate_keys:
        pairs = {}
        for k in plate_keys:
            try:
                plate_w, count = float(k[len("plate_"):]), num(k)
            except (ValueError, inv.WeightSyntaxError):
                errors["plates"] = "Pairs must be whole numbers"
                continue
            if count:
                if count < 0 or count != int(count):
                    errors["plates"] = "Pairs must be whole numbers"
                pairs[plate_w] = int(count)
        inventory["plates"] = inv.plates_text(pairs)
    else:
        inventory["plates"] = str(form.get("plates", "")).strip()
        try:
            inv.parse_plates(inventory["plates"])
        except (inv.WeightSyntaxError, ValueError):
            errors["plates"] = "Use a format like 45x2, 25x2, 10x4"

    for kind, (_, _, spec) in inv.EQUIPMENT_KINDS.items():
        cfg = {"enabled": bool(form.get(f"{kind}_enabled"))}
        if spec == "plates":
            try:
                cfg["bar"] = float(form.get(f"{kind}_bar") or 0)
            except ValueError:
                errors[kind] = "Bar weight must be a number"
        elif spec == "list":
            mode = inv.LIST_INPUT.get(kind)
            try:
                if mode == "range" and f"{kind}_from" in form:
                    raw[kind] = {p: str(form.get(f"{kind}_{p}", "")).strip() for p in ("from", "to", "step")}
                    cfg["weights"] = inv.range_text(num(f"{kind}_from"), num(f"{kind}_to"), num(f"{kind}_step"))
                elif mode == "chips" and f"{kind}_weights" not in form:
                    picked = sorted({float(v) for v in form.getlist(f"{kind}_w")})
                    cfg["weights"] = ", ".join(fmt_weight(w) for w in picked)
                else:
                    cfg["weights"] = str(form.get(f"{kind}_weights", "")).strip()
                    inv.parse_weight_list(cfg["weights"])
            except inv.WeightSyntaxError as err:
                cfg["weights"] = ""
                errors[kind] = str(err) if mode == "range" else "Use a list like 10, 15, 20 or a range like 5-50/5"
            if kind == "cable":
                cfg["pulleys"] = [p for p in inv.PULLEYS if form.get(f"cable_pulley_{p}")]
        inventory[kind] = cfg

    return inventory, raw, errors


@app.post("/gym", response_class=HTMLResponse)
async def save_gym(request: Request):
    form = await request.form()
    inventory, raw, errors = _parse_inventory(form)
    with tx() as conn:
        prof = current_profile(request, conn)
        if not prof:
            return pick_profile()
        if errors:
            s = {**service.settings(conn, prof["id"]), "inventory": inv.merged(inventory)}
            return render(request, "gym.html", **_gym_ctx(conn, prof, s, errors, raw=raw))
        db.set_setting(conn, "inventory", inventory)
    return redirect("/gym?saved=1")


@app.post("/excluded/{exercise_id}/remove")
def unexclude(request: Request, exercise_id: str):
    with tx() as conn:
        p = current_profile(request, conn)
        if not p:
            return pick_profile()
        conn.execute("DELETE FROM excluded WHERE profile_id = ? AND exercise_id = ?", (p["id"], exercise_id))
    return redirect("/settings#excluded")


@app.get("/photos/{name}")
def photo(name: str):
    if not photos.FILENAME_RX.match(name):
        raise HTTPException(status_code=404)
    path = photos.photos_dir() / name
    if not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=604800"})


@app.get("/manifest.webmanifest")
def manifest():
    """Lets Android (and other browsers) install the app with its name and icon.
    iPhone uses the apple-touch-icon link in base.html instead."""
    return JSONResponse({
        "name": "Rackmount",
        "short_name": "Rackmount",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": "#140B24",
        "theme_color": "#140B24",
        "icons": [
            {"src": asset("icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": asset("icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": asset("icon.svg"), "sizes": "any", "type": "image/svg+xml"},
        ],
    }, media_type="application/manifest+json")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Browsers ask for this on their own; answer with the PNG favicon instead of a 404."""
    return FileResponse(HERE / "static" / "favicon-32.png", media_type="image/png",
                        headers={"Cache-Control": "public, max-age=86400"})


@app.get("/healthz")
def healthz():
    return {"ok": True}
