# Fitness Planner — Design Decisions

A self-hosted, Fitbod-inspired workout planner. You tell it what equipment you have, and each day it generates a workout that trains rested muscle groups and lets tired ones recover. It runs in a container on the homelab and is used from a phone browser.

## Feasibility

It's doable. Fitbod's real advantage is exercise data tagged with muscles and equipment, and open data sets for that exist. The generator itself is scoring logic over that data plus your logged history.

## Settled (user decisions)

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | Build vs reuse | **Build a new app** that uses open exercise data | wger has no daily auto-generation, and that is the core feature |
| 2 | Users | **Single user**, no accounts | Personal use only |
| 3 | Remote access | **Existing VPN / Tailscale** | Nothing exposed publicly, so no auth layer is needed |
| 4 | Logging depth | **Per-set: reps and weight** | Drives recovery per muscle (from how much work each did) and weight suggestions |
| 5 | Equipment | **One equipment checklist** (one home gym) | Always trains in the same place |
| 6 | Muscle selection | **Automatic, based on recovery** | Fitbod-style; no split to manage |
| 7 | History import | **Start fresh**, no Fitbod import | The app learns working weights from what you log |
| 8 | Training goal | **Selectable in settings**: strength / muscle building / endurance | Each goal has its own reps, sets and rest time |
| 9 | Workout length | **One fixed setting** (number of exercises or target minutes) | Same length every day |
| 10 | Exercise control | **'Never suggest' exclude list only**, no in-workout swap | Home gym, so equipment is never busy; some exercises just don't suit you |
| 11 | Weight modeling | **Exact inventory** of owned weights | Suggestions are always weights you can actually load |
| 12 | Inventory types | **Common types, entered in the app**: barbell + plates, fixed or adjustable dumbbells, cable stack, kettlebells, bodyweight stations (pull-up bar, dip station), bench, bands | The details are entered in the app, not hard-coded |
| 13 | Workout content | **Strength only** | No generated warm-up, cardio or core |
| 14 | Progress views | **History list + recovery % per muscle** | No charts or body map in v1 |
| 15 | Rest timer | **Auto-start after each logged set**, length set by the goal, **visual only** | Phone browsers can't reliably alert while the screen is locked |
| 16 | Stack | **One container: Python FastAPI + server-rendered pages + SQLite on a mounted volume** | Little build tooling; backing up means copying one file. Plain HTML forms turned out to be enough, so HTMX was not added. |

## Implementer defaults (no user decision needed)

- **Exercise data:** use an open data set with muscle and equipment tags (candidate: free-exercise-db, public domain, ~800 exercises with images). Vet the source before adding it. Its equipment tags get mapped to the app's inventory types.
- **First time doing an exercise:** no weight is suggested; you enter one, and the app progresses from what you log.
- **Recovery model:** each muscle group gets fatigue from the logged volume (sets × reps × weight, with primary muscles weighted more than secondary ones), which wears off over time. A muscle counts as recovered at about 100%.
- **Generation:** pick the most-recovered muscle groups, choose exercises that hit them, use only available equipment, and skip excluded exercises. Reps and sets follow the goal; the weight follows your last performance and is rounded to a weight your inventory can load. A **Regenerate** button is available until the first set is logged.
- **Muscle priority and staples (added during QA):** ranking muscles on recovery alone produced calves/forearms/lower-back days when everything was fresh. Target ranking now adds a priority for big muscle groups, and about 60 well-known lifts get a scoring boost over obscure variations.
- **Station requirements** (bench, rack, pull-up bar, dip station) are inferred from exercise names, because the dataset doesn't tag them. Specialty variants (chains, banded barbell) and the dataset's "machine" exercises are left out.

## Out of scope for v1

Multiple users, public exposure and authentication, multiple gyms, Fitbod import, warm-up/cardio/core, charts/body map, offline/PWA sync, in-workout swaps.
