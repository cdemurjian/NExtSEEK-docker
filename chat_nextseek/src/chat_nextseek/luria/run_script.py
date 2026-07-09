"""Render the Luria SLURM run.sh from a fixed template + validated slots.

The template scaffold (SBATCH directives, module loads, conda activate, the
nextflow invocation) is fixed; only bounded, validated slots are substituted.
`cpus` (the only LLM-proposed resource that reaches the template) is validated
against a strict allow-list; `pipeline`/`revision`/`genome` are allow-listed
fail-closed (`genome` is the species-resolved iGenomes key, NOT hardcoded);
`run_dir`/`work_dir`/`singularity_cache` come from trusted config
(LURIA_WORKING_PATH + a sanitized run name), not LLM free-text.
"""
from __future__ import annotations

import re
from pathlib import Path

DEFAULT_RESOURCES = {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "8G"}

_RES_PATTERNS = {
    "partition": re.compile(r"[A-Za-z0-9_-]{1,32}"),
    "time": re.compile(r"\d{1,3}:\d{2}:\d{2}"),
    "mem": re.compile(r"\d{1,4}[GM]"),
}
_JOB_NAME_STRIP = re.compile(r"[^A-Za-z0-9_.-]+")

_REVISION_RE = re.compile(r"[A-Za-z0-9._/-]{1,64}")
_PIPELINE_RE = re.compile(r"[A-Za-z0-9._:/-]{1,200}")


def validate_revision(revision: str) -> str:
    """Allow-list a pipeline revision; raise ValueError on anything shell-unsafe (fail-closed)."""
    if not revision or not _REVISION_RE.fullmatch(str(revision)):
        raise ValueError(f"invalid pipeline revision {revision!r}")
    return str(revision)


def validate_pipeline(pipeline: str) -> str:
    """Allow-list a pipeline name/URL; raise ValueError on anything shell-unsafe (fail-closed)."""
    if not pipeline or not _PIPELINE_RE.fullmatch(str(pipeline)):
        raise ValueError(f"invalid pipeline {pipeline!r}")
    return str(pipeline)


_GENOME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def validate_genome(genome: str) -> str:
    """Allow-list an iGenomes key (e.g. GRCh38, GRCm39); raise on anything shell-unsafe (fail-closed)."""
    if not genome or not _GENOME_RE.fullmatch(str(genome)):
        raise ValueError(f"invalid genome {genome!r}")
    return str(genome)


_TEMPLATE = Path(__file__).parent / "templates" / "run.sh.tmpl"

_LURIA_CONFIG_TEMPLATE = Path(__file__).parent / "templates" / "luria.config.tmpl"
_REFS_ROOT_RE = re.compile(r"[A-Za-z0-9_./-]{1,256}")


def render_luria_config(refs_root: str) -> str:
    """Render luria.config (the local reference-genomes map) from its template,
    substituting REFS_ROOT := <LURIA_WORKING_PATH>/refs. `refs_root` is trusted
    config (the Luria working_path), not LLM input, but is path-validated
    fail-closed for defense in depth."""
    if not refs_root or not _REFS_ROOT_RE.fullmatch(str(refs_root)):
        raise ValueError(f"invalid refs_root {refs_root!r}")
    text = _LURIA_CONFIG_TEMPLATE.read_text(encoding="utf-8")
    return text.replace("{{REFS_ROOT}}", str(refs_root).rstrip("/"))


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


def render_run_script(*, job_name: str, pipeline: str, revision: str, run_dir: str,
                      work_dir: str, singularity_cache: str, genome: str,
                      resources: dict | None) -> str:
    """Substitute the validated slots into the fixed run.sh template."""
    revision = validate_revision(revision)
    pipeline = validate_pipeline(pipeline)
    genome = validate_genome(genome)
    res = validate_resources(resources)
    mapping = {
        "JOB_NAME": sanitize_job_name(job_name),
        "CPUS": res["cpus"],
        "PARTITION": res["partition"],
        "RUN_DIR": run_dir,
        "PIPELINE": pipeline,
        "REVISION": revision,
        "GENOME": genome,
        "WORK_DIR": work_dir,
        "SINGULARITY_CACHE": singularity_cache,
    }
    out = _TEMPLATE.read_text(encoding="utf-8")
    for token, value in mapping.items():
        out = out.replace("{{" + token + "}}", str(value))
    return out
