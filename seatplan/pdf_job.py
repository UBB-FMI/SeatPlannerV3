"""Resource-limited subprocess entry point. Called only with server-created jobs."""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> None:
    if sys.platform.startswith("linux"):
        import resource
        resource.setrlimit(resource.RLIMIT_CPU, (180, 190))
        resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
        resource.setrlimit(resource.RLIMIT_FSIZE, (200 * 1024**2, 200 * 1024**2))
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    from .detection import detect_seats, render_pdf
    from .domain import DetectSpec
    job = json.loads(Path(sys.argv[1]).read_text())
    try:
        if job["kind"] == "render":
            result = {"pages": render_pdf(Path(job["pdf_path"]), Path(job["output_dir"]), job["max_pages"])}
        elif job["kind"] == "detect":
            result = detect_seats(Path(job["image_path"]), DetectSpec.model_validate(job["spec"]), job.get("zones", []), Path(job["pdf_path"]))
        else:
            raise ValueError("Unknown job type.")
        output = {"ok": True, "result": result}
    except Exception as exc:
        output = {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    Path(sys.argv[2]).write_text(json.dumps(output, allow_nan=False))


if __name__ == "__main__":
    main()
