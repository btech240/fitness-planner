# Rackmount — Design Decisions

A self-hosted workout planner, builder and tracker. You tell it what equipment you have, and each day it generates a workout that trains rested muscle groups and lets tired ones recover. It runs in a container on the homelab and is used from a phone browser.

## Feasibility

It's doable. The key ingredient is exercise data tagged with muscles and equipment, and open data sets for that exist. The generator itself is scoring logic over that data plus your logged history.

## Settled (user decisions)

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| 1 | Build vs reuse | **Build a new app** that uses open exercise data | wger has no daily auto-generation, and that is the core feature |
| 2 | Users | ~~Single user~~ → replaced by #26 | |
| 26 | Household profiles | **"Who's working out?" picker, no passwords.** Each phone remembers its person (cookie); **Switch profile** at the top of Settings. Add person = name + goal + exercises per workout; initial + auto color tile. Rename, and delete with type-the-name confirmation. **Shared:** equipment list. **Personal:** workouts/history, recovery, goal, count, units, pins, never-suggest. Upgrading started everyone fresh (old DB backed up beside it as `fitness.pre-profiles-*.db`), keeping only the equipment. | Other people in the house want their own history without logins |
| 3 | Remote access | **Existing VPN / Tailscale** | Nothing exposed publicly, so no auth layer is needed |
| 4 | Logging depth | **Per-set: reps and weight** | Drives recovery per muscle (from how much work each did) and weight suggestions |
| 5 | Equipment | **One equipment checklist** (one home gym) | Always trains in the same place |
| 6 | Muscle selection | **Automatic, based on recovery** | No split to manage |
| 7 | History import | **Start fresh**, no import from other apps | The app learns working weights from what you log |
| 8 | Training goal | **Selectable in settings**: strength / muscle building / endurance | Each goal has its own reps, sets and rest time |
| 9 | Workout length | **One fixed setting** (number of exercises or target minutes) | Same length every day |
| 10 | Exercise control | ~~'Never suggest' only, no in-workout swap~~ → replaced by #29 | |
| 29 | Changing today's workout | **Swap** (one tap: next-best exercise for the same muscle) and **Remove** (today only) as buttons on the open card, until a set is logged on it; you can't remove the last exercise. **Regenerate** became **New exercises**: same day type, every exercise different, until the first set. Exercises passed over are remembered per workout (`workout_skips`, schema v3) so they don't come back while alternatives exist. **Never suggest** stays for permanent exclusions. Layout W1 picked from two mockups. | Real use showed the need to drop or replace single exercises, and Regenerate kept producing the same workout |
| 11 | Weight modeling | **Exact inventory** of owned weights | Suggestions are always weights you can actually load |
| 12 | Inventory types | **Common types, entered in the app**: barbell + plates, fixed or adjustable dumbbells, cable stack, kettlebells, bodyweight stations (pull-up bar, dip station), bench, bands | The details are entered in the app, not hard-coded |
| 13 | Workout content | **Strength only** | No generated warm-up, cardio or core |
| 14 | Progress views | **History list + recovery % per muscle** | No charts or body map in v1 |
| 15 | Rest timer | **Auto-start after each logged set**, length set by the goal, **visual only** | Phone browsers can't reliably alert while the screen is locked |
| 17 | Visual style | **"Miami Night"**: Athletic Console layout (condensed uppercase headings, mono numbers, set table with SET / PREV / REPS / LB) on deep indigo. Hot pink #FF3EA5 for actions and the active exercise, aqua #2EE6D6 for progress, neon yellow #FFD23F for the stripe and warnings. Dark only, bottom tab bar. | Picked from 15 mockups on the style canvas. Fonts load from Google Fonts with system fallbacks. |
| 25 | Form photos | **free-exercise-db start/end photos at the bottom of How to, side by side at 4:3** (center-cropped; tap to see the full photo). Downloaded once into the data folder by install/update, served by the app. Hidden for Barbell Hip Thrust and Dips – Triceps Version. | Owner vetted the 38 core lifts: 33 clear, 2 not helpful; layout B picked from two mockups |
| 18 | Variety | **Core lifts + rotation**: 2 core lifts per muscle (pinned > most logged > defaults) alternate in the first slot; other slots rotate, avoiding recent and frequent picks | Weights need repeats to progress; rotation keeps it fresh |
| 19 | Day shape | **Recovery-picked patterns**: Push / Pull / Legs / Upper / Lower / Full body, no back-to-back repeat when avoidable | Coherent days without managing a split |
| 20 | Pins | **App picks core lifts; "Pin as core lift" on a card** forces one in whenever its muscle is trained. Pinned list in Settings. | Zero setup, control when wanted |
| 21 | Bodyweight progression | **Reps**: last session average + 1, capped at goal reps + 5, then a hint to add weight | Bodyweight moves have no load to progress |
| 22 | Cable stack | **Pulley positions (High / Low / Mid)** in Settings; all 81 cable exercises hand-tagged. Owner's stack: High + Low. | Mid-pulley moves can't be done on a high/low stack |
| 23 | Remote access (replaces #3) | **Home network only**, `http://<lxc-ip>:8000`; no Tailscale | The app is only used at home |
| 27 | Security posture | **No app login (by design), but nothing beyond the app**: same-origin check on every change (blocks cross-site forms), host allowlist (blocks DNS rebinding), no API docs/schema or server banner; service as `fitness` user with read-only root-owned code, `ProtectSystem=strict`, no capabilities, syscall filter and other systemd sandboxing (auto-fallback if the container can't support it). Container SSH lockdown documented for the owner to apply. See docs/SECURITY.md. | Anyone on the LAN may use the app, but must not be able to act on the container without root |
| 24 | Deployment (phase 2) | **Proxmox LXC, Debian 12, no Docker**: read-only deploy key, `install.sh` (venv + systemd) and `update.sh`; backups via existing Proxmox backups | Matches the homelab |
| 28 | Name | **Rackmount**, shown as the home-screen and tab name. The GitHub repo is `rackmount`; server internals (`/opt/fitness-planner`, the `fitness-planner` service, the `fitness` user) keep their names so existing installs need no migration. | The project needed a name before others could deploy it |
| 30 | Adding exercises | **Add exercise box below the workout, any time before Finish**: **Suggest one** (best fit for the day's least-worked muscle, appended) or **Choose myself** (a picker: today's muscles first, "Other muscles" for the rest, search by name, best first, shows when each was last done). Only gym-supported, non-excluded exercises; no duplicates; no custom exercises. Layout A1/A2 approved from mockups. | Some days you want more volume, on theme or something specific |
| 16 | Stack | **One container: Python FastAPI + server-rendered pages + SQLite on a mounted volume** | Little build tooling; backing up means copying one file. Plain HTML forms turned out to be enough, so HTMX was not added. |

## Implementer defaults (no user decision needed)

- **Exercise data:** use an open data set with muscle and equipment tags (candidate: free-exercise-db, public domain, ~800 exercises with images). Vet the source before adding it. Its equipment tags get mapped to the app's inventory types.
- **First time doing an exercise:** no weight is suggested; you enter one, and the app progresses from what you log.
- **Recovery model:** each muscle group gets fatigue from the logged volume (sets × reps × weight, with primary muscles weighted more than secondary ones), which wears off over time. A muscle counts as recovered at about 100%.
- **Generation:** pick the most-recovered muscle groups, choose exercises that hit them, use only available equipment, and skip excluded exercises. Reps and sets follow the goal; the weight follows your last performance and is rounded to a weight your inventory can load. **New exercises** (same day type, different exercises) is available until the first set is logged; **Swap** and **Remove** per exercise until a set is logged on it.
- **Muscle priority and staples (added during QA):** ranking muscles on recovery alone produced calves/forearms/lower-back days when everything was fresh. Target ranking now adds a priority for big muscle groups, and about 60 well-known lifts get a scoring boost over obscure variations.
- **Station requirements** (bench, rack, pull-up bar, dip station) are inferred from exercise names, because the dataset doesn't tag them. Specialty variants (chains, banded barbell) and the dataset's "machine" exercises are left out.

## Out of scope for v1

Multiple users, public exposure and authentication, multiple gyms, importing from other apps, warm-up/cardio/core, charts/body map, offline/PWA sync.
