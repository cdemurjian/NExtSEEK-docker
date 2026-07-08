"""Luria-direct submitter.

Reads chat_nextseek's emitted launch.yml (the same file the Tower path reads),
renders a run.sh per launch entry, stages run.sh + params.yml + samplesheet.csv
into a per-run remote directory, and `sbatch`es it over SSH. Returns one ref
dict per submitted run (SLURM job id + remote dir + log path).
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

try:
    import yaml as _yaml
except Exception:  # pragma: no cover
    _yaml = None

from .run_script import render_run_script, sanitize_job_name
from .ssh import prepare_key, ssh_run, scp_file

_JOB_ID_RE = re.compile(r"Submitted batch job (\d+)")
_REQUIRED_ENV = ("user", "key", "working_path", "host")


def submit_luria(launch_yml_path, *, luria_env: dict, resources: dict | None = None,
                 job_name: str | None = None, cwd=None) -> list[dict]:
    """Submit each launch.yml entry to Luria via ssh+sbatch. Returns run refs (empty on any skip)."""
    launch_path = Path(launch_yml_path).resolve()
    if not launch_path.exists():
        print(f"[LURIA][SUBMIT] launch.yml not found: {launch_path}")
        return []
    if not all(luria_env.get(k) for k in _REQUIRED_ENV):
        print("[LURIA][SUBMIT] LURIA env incomplete — skipping")
        return []
    if _yaml is None:
        raise RuntimeError("PyYAML is required to parse launch.yml for Luria submission.")

    doc = _yaml.safe_load(launch_path.read_text(encoding="utf-8")) or {}
    entries = doc.get("launch") if isinstance(doc, dict) else None
    if not entries:
        print(f"[LURIA][SUBMIT] no launch entries in {launch_path}")
        return []

    working = str(luria_env["working_path"]).rstrip("/")
    parent = launch_path.parent
    key_path = prepare_key(luria_env["key"])
    runs: list[dict] = []
    try:
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            try:
                ref = _submit_one(entry, idx, parent, working, luria_env, resources, job_name, key_path)
                if ref:
                    runs.append(ref)
            except Exception as exc:
                print(f"[LURIA][SUBMIT] entry {entry.get('name', idx)!r} failed: {exc!r}")
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass
    return runs


def _submit_one(entry, idx, parent, working, luria_env, resources, job_name, key_path):
    name = (entry.get("name") or f"run{idx}").strip() or f"run{idx}"
    pipeline = entry.get("pipeline")
    revision = entry.get("revision")
    if not pipeline or not revision:
        print(f"[LURIA][SUBMIT] entry {name!r} missing pipeline/revision — skipping")
        return None

    params_ref = entry.get("params-file") or "./params.yml"
    params_local = (parent / params_ref).resolve()
    if not params_local.exists():
        print(f"[LURIA][SUBMIT] params file not found: {params_local}")
        return None

    safe = sanitize_job_name(name)
    run_id = datetime.now().strftime("%y%m%d_%H%M%S") + f"_{idx}"
    remote_run_dir = f"{working}/runs/{safe}_{run_id}"
    work_dir = f"{working}/work/{safe}"
    cache_dir = f"{working}/singularity_cache"
    remote_samplesheet = f"{remote_run_dir}/samplesheet.csv"

    params = _yaml.safe_load(params_local.read_text(encoding="utf-8")) or {}
    samplesheet_local = params.get("input")
    if not samplesheet_local or not Path(samplesheet_local).exists():
        print(f"[LURIA][SUBMIT] samplesheet not found for entry {name!r}: {samplesheet_local}")
        return None
    params["input"] = remote_samplesheet  # nextflow reads the remote copy

    params_tmp = run_tmp = None
    try:
        fd, params_tmp = tempfile.mkstemp(prefix="params_", suffix=".yml")
        os.close(fd)
        Path(params_tmp).write_text(_yaml.safe_dump(params, sort_keys=False), encoding="utf-8")

        run_sh = render_run_script(
            job_name=job_name or name, pipeline=pipeline, revision=revision,
            work_dir=work_dir, singularity_cache=cache_dir, resources=resources,
        )
        fd, run_tmp = tempfile.mkstemp(prefix="run_", suffix=".sh")
        os.close(fd)
        Path(run_tmp).write_text(run_sh, encoding="utf-8")

        ssh_run(luria_env, f"mkdir -p {remote_run_dir} {work_dir}", key_path=key_path)
        scp_file(luria_env, run_tmp, f"{remote_run_dir}/run.sh", key_path=key_path)
        scp_file(luria_env, params_tmp, f"{remote_run_dir}/params.yml", key_path=key_path)
        scp_file(luria_env, samplesheet_local, remote_samplesheet, key_path=key_path)
        out = ssh_run(luria_env, f"cd {remote_run_dir} && sbatch run.sh", key_path=key_path)
    finally:
        for f in (params_tmp, run_tmp):
            if f:
                try:
                    os.remove(f)
                except OSError:
                    pass

    m = _JOB_ID_RE.search(out or "")
    job_id = m.group(1) if m else None
    return {
        "job_id": job_id,
        "remote_dir": remote_run_dir,
        "log": f"{remote_run_dir}/nf-{job_id}.out" if job_id else None,
        "run_name": name,
    }
