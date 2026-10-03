"""Downloads the phone's pocket model (Phase 8) into backend/models/pocket, for NOVA to serve.

    backend\\.venv\\Scripts\\python scripts\\fetch_pocket_model.py

The phone never fetches from the internet: it copies the model from the PC over the home
network. Files that are already complete (by size) are skipped, so an interrupted run resumes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

from nova.config import Settings

MODELS = [(Settings.pocket_model, Settings.pocket_model_lib), (Settings.pocket_model_small, Settings.pocket_model_small_lib)]
# The compiled WebGPU programs, from the library version web-llm 0.2.85 expects.
LIBS = "https://raw.githubusercontent.com/mlc-ai/binary-mlc-llm-libs/main/web-llm-models/v0_2_84/base/"
ROOT = Path(__file__).resolve().parent.parent / "backend" / "models" / "pocket"
SKIP = {".gitattributes", "README.md"}


def fetch(client: httpx.Client, url: str, target: Path, size: int | None) -> None:
    if target.exists() and (size is None or target.stat().st_size == size):
        print(f"  have {target.name}")
        return
    partial = target.with_suffix(target.suffix + ".part")
    with client.stream("GET", url, follow_redirects=True) as response:
        response.raise_for_status()
        with partial.open("wb") as out:
            for chunk in response.iter_bytes(1 << 20):
                out.write(chunk)
    if size is not None and partial.stat().st_size != size:
        raise SystemExit(f"{target.name}: got {partial.stat().st_size} bytes, expected {size}")
    partial.replace(target)
    print(f"  got  {target.name} ({target.stat().st_size / 1e6:.1f} MB)")


def main() -> int:
    models = MODELS[1:] if "--small-only" in sys.argv else MODELS
    with httpx.Client(timeout=httpx.Timeout(60.0, read=300.0)) as client:
        for model, lib in models:
            folder = ROOT / model
            folder.mkdir(parents=True, exist_ok=True)
            files = client.get(f"https://huggingface.co/api/models/mlc-ai/{model}/tree/main").json()
            wanted = [f for f in files if f["type"] == "file" and f["path"] not in SKIP]
            print(f"{model}: {len(wanted)} files, {sum(f['size'] for f in wanted) / 1e6:.0f} MB")
            for item in wanted:
                url = f"https://huggingface.co/mlc-ai/{model}/resolve/main/{item['path']}"
                fetch(client, url, folder / item["path"], item["size"])
            (ROOT / "libs").mkdir(exist_ok=True)
            fetch(client, LIBS + lib, ROOT / "libs" / lib, None)
    print("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
