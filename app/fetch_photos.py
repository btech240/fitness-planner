"""Download exercise photos into the photos folder. Safe to re-run: existing files are skipped.

    python -m app.fetch_photos            # into photos_dir() (beside the database)
    python -m app.fetch_photos /some/dir

About 950 JPEGs, ~60 MB. Only the standard library is used.
"""

from __future__ import annotations

import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import catalog, photos

MAX_BYTES = 2 * 1024 * 1024
TIMEOUT = 20


def _fetch(task: tuple[Path, str]) -> str:
    dest, url = task
    if dest.is_file() and dest.stat().st_size > 0:
        return "skipped"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fitness-planner-photo-fetch"})
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            if resp.headers.get_content_type() != "image/jpeg":
                return "failed"
            data = resp.read(MAX_BYTES + 1)
        if not data or len(data) > MAX_BYTES:
            return "failed"
        tmp = dest.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return "downloaded"
    except OSError:
        return "failed"


def main(argv: list[str]) -> int:
    folder = Path(argv[1]) if len(argv) > 1 else photos.photos_dir()
    folder.mkdir(parents=True, exist_ok=True)
    tasks = [
        (folder / photos.filename(e["id"], n), photos.SOURCE_URL.format(id=e["id"], n=n))
        for e in catalog.EXERCISES if e["id"] not in photos.HIDDEN
        for n in range(min(2, e.get("photos", 0)))
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(_fetch, tasks))
    counts = {k: results.count(k) for k in ("downloaded", "skipped", "failed")}
    print(f"photos in {folder}: {counts['downloaded']} downloaded, {counts['skipped']} already there, "
          f"{counts['failed']} failed")
    return 0  # a few missing photos never block an install or update


if __name__ == "__main__":
    sys.exit(main(sys.argv))
