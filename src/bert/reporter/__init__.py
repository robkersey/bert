"""Report renderers."""

from pathlib import Path

from bert.reporter import html, junit, markdown
from bert.runner.core import RunResult


def write_all(result: RunResult, run_dir: Path, *, formats: set[str] | None = None) -> dict[str, Path]:
    """Render the requested formats into ``run_dir``. Returns paths written."""

    formats = formats or {"junit", "markdown", "html"}
    out: dict[str, Path] = {}
    if "junit" in formats:
        p = run_dir / "report.junit.xml"
        junit.render(result, path=p)
        out["junit"] = p
    if "markdown" in formats or "md" in formats:
        p = run_dir / "report.md"
        markdown.render(result, path=p)
        out["markdown"] = p
    if "html" in formats:
        p = run_dir / "report.html"
        html.render(result, path=p)
        out["html"] = p
    return out


__all__ = ["html", "junit", "markdown", "write_all"]
