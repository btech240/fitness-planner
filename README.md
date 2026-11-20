# Fitness Planner

A self-hosted, Fitbod-style workout generator. You enter the equipment in your home gym, and each day it builds a workout around the most-recovered muscle groups. You log sets, reps and weight, and it suggests the next weights from what you logged. It's a single container with a SQLite file, and you use it from your phone's browser.

Design decisions: [docs/DECISIONS.md](docs/DECISIONS.md).

## Run it on the homelab

```sh
git clone <this repo> fitness-planner && cd fitness-planner   # or copy the folder over
mkdir -p data && sudo chown 1000:1000 data                       # container runs as uid 1000
docker compose up -d --build
```

Open `http://<homelab-host>:8000` from your phone over Tailscale/VPN. On iPhone, Share → **Add to Home Screen** gives it an app icon and full-screen view.

Edit `TZ` in `compose.yaml` so History shows your local times.

**First run:** go to **Settings**, tick the equipment you own, enter the exact weights (`5-50/5` for a range, `35, 53` for a list, plates as `45x2, 25x2, 10x4`), tick your stations (bench, rack, pull-up bar, dip station), and pick your goal.

**Backups:** everything is in `data/fitness.db`. Copy that file (with the container stopped, or use `sqlite3 data/fitness.db ".backup backup.db"`).

There is no login, so don't expose port 8000 to the internet. Keep it behind your VPN.

## How it decides

- **Recovery:** each logged set adds fatigue to the muscles it works (primary 1.0, secondary 0.5), which fades over 72 hours. 10 sets takes a muscle to 0%. Muscles at 60% or above can be picked.
- **Workout:** it picks up to 3 target muscles, ranked by recovery plus a priority for big muscle groups. It fills exercises round-robin across those targets, favoring well-known staple lifts and ones you've done before, with some randomness so days vary. Exercises on the "never suggest" list are skipped.
- **Weights:** it estimates your one-rep max from last session's best set and converts it to today's rep target. If you hit every rep last time, it goes up one loadable step (at most +15%). Suggestions are always weights your inventory can actually load. The first time you do an exercise, you choose the weight.

Tuning knobs are constants at the top of `app/generator.py` and `app/recovery.py`.

## Develop

```sh
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt     # .venv/bin/pip on Linux/macOS
.venv/Scripts/python -m pytest
.venv/Scripts/python -m uvicorn app.main:app --port 8000
```

Exercise data comes from [free-exercise-db](https://github.com/yuhonas/free-exercise-db) (public domain). To refresh it, see `scripts/build_exercises.py`.
