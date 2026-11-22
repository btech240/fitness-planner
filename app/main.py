"""Web routes. Server-rendered pages with plain HTML forms (POST, then redirect)."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import catalog, db, generator, inventory as inv, service
from .recovery import TARGET_MUSCLES

HERE = Path(__file__).parent
app = FastAPI(title="Fitness Planner", docs_url=None, redoc_url=None)
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


templates.env.filters["w"] = fmt_weight
templates.env.filters["setfmt"] = fmt_set
templates.env.globals["goal_codes"] = {"strength": "STR", "hypertrophy": "HYP", "endurance": "END"}
templates.env.filters["local"] = local_time


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


# ---- today -------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def today(request: Request):
    with tx() as conn:
        s = service.settings(conn)
        w = service.current_workout(conn)
        rec = service.current_recovery(conn)
        ctx = {"s": s, "workout": w, "recovery": rec}
        if w:
            items = service.workout_detail(conn, w["id"])
            targets = []
            for it in items:
                for m in it["exercise"]["primary"]:
                    if m not in targets:
                        targets.append(m)
            ctx.update(
                items=items,
                active_id=service.active_exercise_id(items),
                pattern_label=generator.PATTERNS[w["pattern"]][0] if w["pattern"] in generator.PATTERNS else None,
                targets=[(m, rec.get(m, 100)) for m in targets[:3]],
                has_sets=service.workout_has_sets(conn, w["id"]),
                last_set=service.last_set_time(conn, w["id"]),
                rest=generator.GOALS[w["goal"]].rest_seconds,
            )
        else:
            ctx["pool_size"] = len(service._pool(conn, s))
            ctx["ready"] = sorted(((m, rec[m]) for m in TARGET_MUSCLES), key=lambda x: -x[1])
    return render(request, "today.html", **ctx)


@app.post("/workout/new")
def new_workout():
    with tx() as conn:
        if not service.current_workout(conn):
            service.create_workout(conn)
    return redirect("/")


@app.post("/workout/{wid}/regenerate")
def regenerate(wid: int):
    with tx() as conn:
        if not service.workout_has_sets(conn, wid):
            conn.execute("DELETE FROM workouts WHERE id = ?", (wid,))
            service.create_workout(conn)
    return redirect("/")


@app.post("/workout/{wid}/discard")
def discard(wid: int):
    with tx() as conn:
        conn.execute("DELETE FROM workouts WHERE id = ? AND finished_at IS NULL", (wid,))
    return redirect("/")


@app.post("/workout/{wid}/finish")
def finish(wid: int):
    with tx() as conn:
        if service.workout_has_sets(conn, wid):
            conn.execute("UPDATE workouts SET finished_at = ? WHERE id = ?", (service.now().isoformat(), wid))
        else:
            conn.execute("DELETE FROM workouts WHERE id = ?", (wid,))
    return redirect("/history")


@app.post("/we/{we_id}/set")
def log_set(we_id: int, reps: int = Form(...), weight: str = Form("")):
    try:
        weight_val = float(weight) if weight.strip() else None
    except ValueError:
        weight_val = None
    with tx() as conn:
        conn.execute(
            "INSERT INTO sets(workout_exercise_id, reps, weight, logged_at) VALUES (?, ?, ?, ?)",
            (we_id, max(0, reps), weight_val, service.now().isoformat()),
        )
    return redirect(f"/#ex-{we_id}")


@app.post("/set/{set_id}/delete")
def delete_set(set_id: int):
    with tx() as conn:
        row = conn.execute("SELECT workout_exercise_id FROM sets WHERE id = ?", (set_id,)).fetchone()
        conn.execute("DELETE FROM sets WHERE id = ?", (set_id,))
    return redirect(f"/#ex-{row['workout_exercise_id']}" if row else "/")


@app.post("/we/{we_id}/pin")
def pin(we_id: int):
    with tx() as conn:
        row = conn.execute("SELECT exercise_id FROM workout_exercises WHERE id = ?", (we_id,)).fetchone()
        if row:
            service.toggle_pin(conn, row["exercise_id"])
    return redirect(f"/#ex-{we_id}")


@app.post("/pinned/{exercise_id}/remove")
def unpin(exercise_id: str):
    with tx() as conn:
        conn.execute("DELETE FROM pinned WHERE exercise_id = ?", (exercise_id,))
    return redirect("/settings#pinned")


@app.post("/we/{we_id}/exclude")
def exclude(we_id: int):
    with tx() as conn:
        service.exclude_and_replace(conn, we_id)
    return redirect("/")


# ---- history & recovery -------------------------------------------------------

@app.get("/history", response_class=HTMLResponse)
def history(request: Request):
    with tx() as conn:
        s = service.settings(conn)
        workouts = conn.execute(
            "SELECT * FROM workouts WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 50").fetchall()
        entries = [{"w": w, "lines": service.workout_detail(conn, w["id"])} for w in workouts]
    return render(request, "history.html", s=s, entries=entries)


@app.get("/recovery", response_class=HTMLResponse)
def recovery_page(request: Request):
    with tx() as conn:
        rec = service.current_recovery(conn)
    rows = sorted(((m, rec[m]) for m in TARGET_MUSCLES), key=lambda x: (-x[1], x[0]))
    return render(request, "recovery.html", rows=rows)


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


def _settings_ctx(conn, s, errors=None, saved=False, raw=None):
    preview = {}
    for kind in inv.EQUIPMENT_KINDS:
        try:
            ws = inv.loadable_weights(s["inventory"], kind)
        except inv.WeightSyntaxError:
            ws = None
        if ws:
            preview[kind] = ws
    excl = sorted((catalog.get(i) for i in service.excluded_ids(conn)), key=lambda e: e["name"])
    pins = sorted((catalog.get(i) for i in service.pinned_ids(conn)), key=lambda e: e["name"])
    return {"s": s, "kinds": inv.EQUIPMENT_KINDS, "stations": inv.STATIONS, "pulleys": inv.PULLEYS,
            "preview": preview, "excluded": excl, "pinned": pins, "errors": errors or {}, "saved": saved,
            "pool_size": len(service._pool(conn, s)) if not errors else None,
            **_equipment_inputs(s, raw)}


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: int = 0):
    with tx() as conn:
        ctx = _settings_ctx(conn, service.settings(conn), saved=bool(saved))
    return render(request, "settings.html", **ctx)


@app.post("/settings", response_class=HTMLResponse)
async def save_settings(request: Request):
    form = await request.form()
    errors: dict[str, str] = {}
    goal = form.get("goal") if form.get("goal") in generator.GOALS else "hypertrophy"
    try:
        n = min(10, max(2, int(form.get("n_exercises", 5))))
    except ValueError:
        n = 5
    units = "kg" if form.get("units") == "kg" else "lb"

    def num(key: str) -> float | None:
        v = str(form.get(key, "")).strip()
        if not v:
            return None
        try:
            return float(v)
        except ValueError:
            raise inv.WeightSyntaxError("Numbers only") from None

    inventory = {"stations": [k for k in inv.STATIONS if form.get(f"station_{k}")]}
    raw: dict[str, dict] = {}

    # plates: one "pairs owned" box per size (legacy free-text "plates" field still accepted)
    plate_keys = [k for k in form.keys() if k.startswith("plate_")]
    if plate_keys:
        pairs = {}
        for k in plate_keys:
            try:
                w, n = float(k[len("plate_"):]), num(k)
            except (ValueError, inv.WeightSyntaxError):
                errors["plates"] = "Pairs must be whole numbers"
                continue
            if n:
                if n < 0 or n != int(n):
                    errors["plates"] = "Pairs must be whole numbers"
                pairs[w] = int(n)
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

    with tx() as conn:
        if errors:
            s = {"goal": goal, "n_exercises": n, "units": units, "inventory": inv.merged(inventory)}
            return render(request, "settings.html", **_settings_ctx(conn, s, errors, raw=raw))
        db.set_setting(conn, "goal", goal)
        db.set_setting(conn, "n_exercises", n)
        db.set_setting(conn, "units", units)
        db.set_setting(conn, "inventory", inventory)
    return redirect("/settings?saved=1")


@app.post("/excluded/{exercise_id}/remove")
def unexclude(exercise_id: str):
    with tx() as conn:
        conn.execute("DELETE FROM excluded WHERE exercise_id = ?", (exercise_id,))
    return redirect("/settings#excluded")


@app.get("/healthz")
def healthz():
    return {"ok": True}
