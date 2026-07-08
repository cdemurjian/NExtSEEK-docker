"""Render the Luria SLURM run.sh from a fixed template + validated resource slots.

The template scaffold (module loads, conda activate, the nextflow invocation
shape) is fixed; only a bounded, validated set of resource/identity slots are
substituted. Only the resource slots (partition/time/cpus/mem) are LLM-proposed,
and each is validated against a strict allow-list before use. `pipeline`,
`revision`, `work_dir`, and `singularity_cache` come from trusted catalog/config
sources, not LLM free-text.
"""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_RESOURCES = {"partition": "bcc", "time": "48:00:00", "cpus": "2", "mem": "8G"}

_RES_PATTERNS = {
    "partition": re.compile(r"[A-Za-z0-9_-]{1,32}"),
    "time": re.compile(r"\d{1,3}:\d{2}:\d{2}"),
    "mem": re.compile(r"\d{1,4}[GM]"),
}
_JOB_NAME_STRIP = re.compile(r"[^A-Za-z0-9_.-]+")

_TEMPLATE = Path(__file__).parent / "templates" / "run.sh.tmpl"


def validate_resources(resources: dict | None) -> dict:
    """Return a full resource dict; each field taken from `resources` only if valid, else default."""
    resources = resources if isinstance(resources, dict) else {}
    out = dict(DEFAULT_RESOURCES)
    for key in ("partition", "time", "mem"):
        val = resources.get(key)
        if val is not None and _RES_PATTERNS[key].fullmatch(str(val)):
            out[key] = str(val)
    try:
        cpus = int(resources.get("cpus"))
        if 1 <= cpus <= 64:
            out["cpus"] = str(cpus)
    except (TypeError, ValueError):
        pass
    return out


def sanitize_job_name(name: str, fallback: str = "nfcore_run") -> str:
    """Reduce an arbitrary name to a SLURM-safe token."""
    cleaned = _JOB_NAME_STRIP.sub("_", (name or "").strip())[:64].strip("_")
    return cleaned or fallback


def render_run_script(*, job_name: str, pipeline: str, revision: str,
                      work_dir: str, singularity_cache: str, resources: dict | None) -> str:
    """Substitute the validated slots into the fixed run.sh template."""
    res = validate_resources(resources)
    mapping = {
        "JOB_NAME": sanitize_job_name(job_name),
        "PARTITION": res["partition"],
        "TIME": res["time"],
        "CPUS": res["cpus"],
        "MEM": res["mem"],
        "PIPELINE": pipeline,
        "REVISION": revision,
        "WORK_DIR": work_dir,
        "SINGULARITY_CACHE": singularity_cache,
    }
    out = _TEMPLATE.read_text(encoding="utf-8")
    for token, value in mapping.items():
        out = out.replace("{{" + token + "}}", str(value))
    return out
