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

_REFS_ROOT_RE = re.compile(r"[A-Za-z0-9_./-]{1,256}")

# Single source of truth for local reference genomes on Luria: genome key -> reference
# filenames under {LURIA_WORKING_PATH}/refs. Drives BOTH the luria.config genomes map and the
# explicit --fasta/--gtf CLI flags — because the map's params.genomes resolution is unreliable
# in Nextflow, submit_to_luria passes the paths directly (path > iGenomes, globally). Keys MUST
# match the igenomes_key values in reports/templates/nfcore/reference_bundles.json.
LURIA_GENOMES: dict[str, dict[str, str]] = {
    "GRCh38":  {"fasta": "GRCh38.primary_assembly.genome.fa.gz",
                "gtf":   "gencode.v46.basic.annotation.gtf.gz"},
    "GRCm39":  {"fasta": "GRCm39.primary_assembly.genome.fa.gz",
                "gtf":   "gencode.vM39.basic.annotation.gtf.gz"},
    "Mfas6.0": {"fasta": "Macaca_fascicularis.Macaca_fascicularis_6.0.dna.toplevel.fa.gz",
                "gtf":   "Macaca_fascicularis.Macaca_fascicularis_6.0.116.gtf.gz"},
    "Mmul_10": {"fasta": "Macaca_mulatta.Mmul_10.dna.toplevel.fa.gz",
                "gtf":   "Macaca_mulatta.Mmul_10.116.gtf.gz"},
}

_LURIA_CONFIG_HEADER = (
    "// luria.config — local reference genomes for nf-core on MIT Luria.\n"
    "// Generated from luria.run_script.LURIA_GENOMES. The params.genomes MAP resolution is\n"
    "// unreliable in Nextflow (getGenomeAttribute returns the value but the workflow assertion\n"
    "// sees empty), so submit_to_luria ALSO passes explicit --fasta/--gtf on the CLI — that is\n"
    "// what actually wires the references. This map is kept for --genome key identity.\n"
)


def genome_ref_paths(genome: str, refs_root: str) -> tuple[str | None, str | None]:
    """Absolute (fasta, gtf) for a genome key under refs_root, or (None, None) when the genome
    has no local refs registered (caller then falls back to --genome/iGenomes)."""
    ref = LURIA_GENOMES.get(str(genome or ""))
    if not ref:
        return (None, None)
    root = str(refs_root or "").rstrip("/")
    return (f"{root}/{ref['fasta']}", f"{root}/{ref['gtf']}")


def render_luria_config(refs_root: str) -> str:
    """Generate luria.config (the local reference-genomes map) from LURIA_GENOMES.
    `refs_root` (:= <LURIA_WORKING_PATH>/refs) is trusted config, path-validated fail-closed."""
    if not refs_root or not _REFS_ROOT_RE.fullmatch(str(refs_root)):
        raise ValueError(f"invalid refs_root {refs_root!r}")
    root = str(refs_root).rstrip("/")
    lines = [_LURIA_CONFIG_HEADER, "params {", "    genomes {"]
    for key, ref in LURIA_GENOMES.items():
        lines += [f"        '{key}' {{",
                  f"            fasta = '{root}/{ref['fasta']}'",
                  f"            gtf   = '{root}/{ref['gtf']}'",
                  "        }"]
    lines += ["    }", "}", ""]
    return "\n".join(lines)


_PROC_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,64}")
_EXT_ARGS_RE = re.compile(r"[A-Za-z0-9_.,=:/ -]{1,200}")

# A -c config REPLACES a process's ext.args (Nextflow can't append across configs — VERIFIED on
# Luria: a self-referencing closure StackOverflows, a plain string clobbers). So overriding ext.args
# is only safe for processes whose PIPELINE default is empty (e.g. alevin's SIMPLEAF_QUANT). These
# processes have a NON-empty default in nf-core/scrnaseq — overriding them would drop needed flags.
# Tune them via the samplesheet instead: STAR (STAR_ALIGN) cell-calling reads `expected_cells` and
# otherwise uses STAR's built-in knee-based --soloCellFilter, so it needs no ext.args override.
_CLOBBER_UNSAFE_PROCESSES = {"STAR_ALIGN"}


def render_process_config(process_args: dict[str, str] | None) -> str:
    """Render a Nextflow process-scope config from {PROCESS_NAME: ext_args}, e.g.
    {'SIMPLEAF_QUANT': '--knee'} -> `process { withName: '.*:SIMPLEAF_QUANT' { ext.args='--knee' } }`.
    The DATA is declared by the curated per-pipeline JSON ('protocol_process_args'), NOT hardcoded
    here — this is just the renderer. Names/args validated fail-closed; raises for a process whose
    pipeline ext.args default is non-empty (would be clobbered). '' for empty input.

    (e.g. scrnaseq seqwell/dropseq declares SIMPLEAF_QUANT '--knee': 2.7.1 needs a cell-calling
    mode and its unfiltered fallback wants a barcode whitelist bead protocols lack. STAR is fine
    without an override — it tunes cell-calling via the samplesheet `expected_cells` column.)"""
    if not process_args:
        return ""
    blocks = []
    for proc, args in process_args.items():
        if not _PROC_NAME_RE.fullmatch(str(proc)):
            raise ValueError(f"invalid process name {proc!r}")
        if str(proc) in _CLOBBER_UNSAFE_PROCESSES:
            raise ValueError(
                f"process {proc!r} has a non-empty pipeline ext.args default a -c override would "
                "clobber (Nextflow can't append); tune it via the samplesheet (e.g. expected_cells) instead")
        if not _EXT_ARGS_RE.fullmatch(str(args)):
            raise ValueError(f"invalid ext.args {args!r}")
        blocks.append(f"    withName: '.*:{proc}' {{\n        ext.args = '{args}'\n    }}")
    return "\nprocess {\n" + "\n".join(blocks) + "\n}\n"


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
                      resources: dict | None, refs_root: str | None = None) -> str:
    """Substitute the validated slots into the fixed run.sh template. When `refs_root` is given
    and `genome` has local refs registered (LURIA_GENOMES), explicit --fasta/--gtf flags are
    injected (path > iGenomes globally; the genomes-map resolution is unreliable)."""
    revision = validate_revision(revision)
    pipeline = validate_pipeline(pipeline)
    genome = validate_genome(genome)
    res = validate_resources(resources)
    refs_flags = ""
    if refs_root:
        if not _REFS_ROOT_RE.fullmatch(str(refs_root)):
            raise ValueError(f"invalid refs_root {refs_root!r}")
        fasta, gtf = genome_ref_paths(genome, refs_root)
        if fasta and gtf:
            refs_flags = f"--fasta {fasta} --gtf {gtf}"
    mapping = {
        "JOB_NAME": sanitize_job_name(job_name),
        "CPUS": res["cpus"],
        "PARTITION": res["partition"],
        "RUN_DIR": run_dir,
        "PIPELINE": pipeline,
        "REVISION": revision,
        "GENOME": genome,
        "REFS_FLAGS": refs_flags,
        "WORK_DIR": work_dir,
        "SINGULARITY_CACHE": singularity_cache,
    }
    out = _TEMPLATE.read_text(encoding="utf-8")
    for token, value in mapping.items():
        out = out.replace("{{" + token + "}}", str(value))
    return out
