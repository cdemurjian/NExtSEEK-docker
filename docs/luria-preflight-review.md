# LURIA First Live-Submit — Consolidated Pre-Flight Report

> Generated 2026-07-09 by the `luria-pipeline-preflight-review` workflow (6 parallel reviewers + Opus synthesis, read-only). Companion to `docs/superpowers/specs/2026-07-08-luria-launch-mode-design.md`.

Synthesis of six parallel reviews (control-flow, samplesheet-data, runsh-execution, config-routing, failure-modes, live-luria-env). All unit tests are green across reviewers (57–94 passing depending on scope). The happy-path plumbing is sound; the danger is in three code-verified gaps plus one unverified infra fact. Do not fire blind.

---

## 1. End-to-end flow (verified)

1. **orchestrator → pipeline_agent gate.** `orchestrator._handle_pipeline_agent_turn` gates on `is_active`; NFCORE classification → `pipeline_agent.start()`. **CONFIRMED**. Once active, *every* subsequent message routes into the tool loop; only an exact-match cancel token or LLM `conclude` exits.
2. **Bedrock tool loop, per-config tools.** `agent.py::_run_loop` (MAX_ITER=12) exposes `submit_to_luria` iff `LURIA_ENV_COMPLETE` and `submit_to_tower` iff `TOWER_ENV_COMPLETE`. Both true here → **BOTH exposed**. `{launch_mode}` injects a literal "call submit_to_luria" default. **GAP:** ambiguous "submit" routing is prompt-enforced only, no code `tool_choice` gate; a mis-route fires a real Tower job.
3. **resolve_samples → write_samplesheet.** Sets `state["artifacts"]["samplesheet"]` to a real local container path. **CONFIRMED local & scp-able**. **GAP:** `fastq_1/fastq_2` filled **only** for accession-bearing rows via ENA; no local-fastq fallback.
4. **configure_run → launch.yml.** Sets `state["artifacts"]["launch"]`. **CONFIRMED it writes** because `tower_complete` is true. **GAP (latent):** emission gated on Tower completeness, not Luria's. **Side-effect:** every `configure_run` makes a real Tower dataset-upload API call even in Luria mode.
5. **submit_to_luria → submit_luria.** Guards cleanly on missing artifact / Luria-not-configured. **GAP:** loops **every** launch.yml entry with the **same** samplesheet — a fetchngs-fallback entry gets sbatch'd with the wrong sheet/flags.
6. **submit_luria per-entry.** Fresh timestamped `run_dir`, `mkdir` runs/work, scp run.sh + samplesheet, `sbatch run.sh`, parse "Submitted batch job N". **GAPs:** no idempotency guard; sbatch exit-0-without-banner → `ok:true` with `job_id:null`.
7. **run.sh on Luria.** module miniconda3/v4 → conda activate `cdemu_nfcore` (Nextflow 26.04.4) → singularity → `nextflow run … -profile singularity`. **GAPs:** no `set -euo pipefail`; module/conda init untested in a **non-interactive batch shell**.
8. **nextflow run.** `--input samplesheet.csv` (relative, after cd — **CONFIRMED correct**), `--genome GRCh38` (**HARDCODED**), local executor on the single `-N1 -n16` node. **GAPs:** genome hardcoded regardless of species; partition/time/mem never reach the script; no `--max_cpus/--max_memory` caps.

---

## 2. Blockers (must fix/verify before ANY live submit)

### B1 — Compute-node internet egress is UNVERIFIED (top go/no-go)
The `-N1` compute node (where Nextflow actually runs — no SLURM executor) must reach S3 (iGenomes GRCh38 — no local cache exists), container registries (quay.io/docker.io), and ENA HTTPS (fastq). If compute nodes are firewalled (common HPC pattern), the job **hangs** rather than failing cleanly. Login-node egress works; compute-node egress is only circumstantial (zero prior nextflow jobs in 525-job history). **Verify:** `sbatch -p normal -N1 -n1 -t 5 --wrap='curl -sS -o /dev/null -w "%{http_code}\n" https://ngi-igenomes.s3.amazonaws.com; curl -sS -o /dev/null -w "%{http_code}\n" https://quay.io/v2/'` then read `.out`. **BLOCKING.**

### B2 — Samplesheet ships blank `fastq_1` for un-accessioned samples
`fastq_1/fastq_2` are populated **only** for rows with a resolvable SRA/ENA accession (`emitter.py:536-559`). No code path maps a NExtSEEK internal raw-fastq sample to a path. nf-core/rnaseq requires `fastq_1` (exists:true) — a blank cell is rejected within seconds. An internal/unpublished mouse cohort = near-certain instant failure. **Verify at build time:** every row must have a well-formed `https://…fastq.gz`. **BLOCKING.**

### B3 — `--genome GRCh38` is hardcoded; species resolution never reaches run.sh
`run.sh.tmpl:24` has the literal `--genome GRCh38`; `render_run_script` has no genome param. The species→bundle resolution `configure_run` computes (mouse→GRCm39) is **cosmetic** on the Luria path. For "rnaseq on these mice," the job aligns mouse reads to **human GRCh38** — silent-wrong-science that still "completes." **Fix:** confirm the first cohort is human, OR thread genome from launch.yml into the template. **BLOCKING for any non-human cohort.**

### B4 — SLURM partition/time/mem are computed but silently dropped
`validate_resources()` computes partition/time(48h default)/mem, but `render_run_script` wires **only CPUS**. Every job runs under Luria's bare default walltime; a from-scratch rnaseq run is multi-hour and may be TIMEOUT-killed. `normal` is the default partition (account/partition acceptance cleared) but its default/max walltime is unknown. **Verify:** `scontrol show partition normal | grep -iE 'DefaultTime|MaxTime'`; if short, wire `--time` into the template. **BLOCKING (walltime unknown).**

---

## 3. Risks (likely to bite, not certain) — ranked

1. **iGenomes GRCh38 downloads at runtime** (no local cache, no `NXF_IGENOMES_BASE`). Long first-run fetch even with egress. *Mitigate:* ask BMC for a shared igenomes tree; else don't mistake the download for a hang.
2. **fetchngs double-submit.** If `write_samplesheet` excludes any accession, a second (fetchngs) launch.yml entry gets sbatch'd with the wrong sheet/flags → doomed second job + failure email. *Mitigate:* proceed only when `excluded_accessions == []`; treat >1 launch entry as a stop.
3. **No `set -euo pipefail`; module/conda untested in a batch shell.** Failed init → `nextflow: command not found`. *Mitigate:* add `set` guard; smoke-test module/conda inside a batch job.
4. **Local executor may OOM.** No `--max_cpus/--max_memory`. STAR index on GRCh38 wants 32GB+. *Mitigate:* pass `--max_cpus 16 --max_memory '<node-RAM>'`.
5. **`-resume` no-op across retries.** `run_dir` (resume cache) is minted fresh each submit. *Mitigate:* reuse the prior run_dir for a retry, or accept full re-run.
6. **False-positive job_id / no idempotency / trust-based conclude.** *Mitigate:* trust only a real integer `job_id` confirmed in `squeue -u cdemu`.
7. **Ambiguous "submit" could fire Tower.** *Mitigate:* say "submit to Luria" explicitly; confirm the fired tool name.

*Cleared by live-env:* disk (31TB free), directory writability, default partition/account acceptance, singularity cache dir.

---

## 4. PRE-FLIGHT CHECKLIST

**A. Config / code baseline**
- [x] Live container env has `PIPELINE_LAUNCH_MODE=LURIA`, `LURIA_USER=cdemu`, `LURIA_WORKING_PATH`, `LURIAKEY` — **PASS**
- [x] Config/prompt unit tests (`test_pipeline_tool_exposure`, `test_pipeline_agent_prompt`) — **PASS** 8/8

**B. Samplesheet build inspection (at build time, before confirming "submit")**
- [ ] Every target leaf has a resolvable accession (inspect `resolve_samples` JSON) — **BLOCKING (B2)**
- [ ] `excluded_accessions == []` (no silent drops, no fetchngs double-submit) — **BLOCKING (B2 + risk 2)**
- [ ] `cat` the emitted `samplesheet.csv` — every `fastq_1` a well-formed `https://…fastq.gz`, valid `strandedness` — **BLOCKING (B2)**
- [ ] Cohort is **human** (GRCh38 correct) OR patch/hand-edit run.sh for the real genome — **BLOCKING (B3)**
- [ ] `launch.yml` has **exactly ONE** entry with pipeline+revision+name — **BLOCKING**

**C. Luria environment (read-only ssh)**
- [x] Disk space (`df -h pipeline_cd`) — **PASS** (31TB free)
- [x] Default partition/account (`scontrol show partition normal`) — **PASS** (normal Default, AllowAccounts=ALL)
- [x] Dir writability (mkdir runs/work/singularity_cache + test -w) — **PASS**
- [x] conda/module chain (login shell) — **PASS** (Nextflow 26.04.4)
- [ ] **Default/max walltime for `normal`** (`scontrol show partition normal | grep -iE 'DefaultTime|MaxTime'`) — **UNKNOWN, BLOCKING (B4)**
- [ ] **Compute-node egress** — smoke `sbatch` curl to S3 + quay.io — **UNKNOWN, BLOCKING (B1)**
- [ ] **conda/module inside a batch shell** — smoke `sbatch` module/conda/which nextflow — **UNKNOWN, BLOCKING (risk 3)**

**D. At-submit confirmation (during the live turn)**
- [ ] Exactly one `submit_to_luria` (not `submit_to_tower`) tool_use block — **BLOCKING**
- [ ] tool_result shows `ok:true` + real integer `job_id`, cross-checked with `squeue -u cdemu` — **BLOCKING**

---

## 5. First-run failure playbook

| # | Symptom | One place to look | Fix |
|---|---------|-------------------|-----|
| 1 | Fails in **seconds** | job `.err` / `.nextflow.log`: "fastq_1 … does not exist" | Blank fastq (B2) — cohort has no accession |
| 2 | **Hangs** with no progress | `.command.log` stuck on container pull / S3 fetch | Compute-node egress (B1) — pre-stage containers + reference |
| 3 | Completes with garbage / low mapping | MultiQC STAR %; the `--genome` line in `run.sh` | Wrong genome (B3) — mouse aligned to GRCh38 |
| 4 | Killed mid-run after hours | job `.out`: "DUE TO TIME LIMIT"; `sacct` State=TIMEOUT | Walltime too short (B4) — wire `--time` |
| 5 | `nextflow: command not found` at start | job `.err` first lines | Module/conda init failed in batch shell — add `set -euo pipefail` |
| 6 | Task killed, exit 137 | that task's `.command.err`: "Killed" | OOM on STAR index (risk 4) — add `--max_memory`/`--max_cpus` |

A **second unexpected job id** in the tool_result = fetchngs double-submit (risk 2) — `scancel` the extra; it doesn't affect the real run.

---

## 6. GO / NO-GO

**Verdict: CONDITIONAL GO** — not ready to fire blind, but ready once the BLOCKING checklist passes AND the cohort is the right shape. Control-flow, config, tool-exposure, samplesheet-locality, and launch.yml plumbing are all confirmed correct and well-tested. There are **no unconditional code blockers** — every blocker is either cohort-conditional (B2, B3, catchable by a samplesheet/species inspection) or an unverified infra fact (B1, B4, resolvable with two tiny read-only smoke sbatches).

**Single thing most likely to fail the first run:** compute-node internet egress (B1) — unverified, gates fastq + iGenomes + singularity pulls simultaneously, fails as a silent hang.

**Smallest safe first test:** a **2-sample, human, public-accession** rnaseq cohort (a known ERR/SRR pair). Neutralizes three blockers at once — fastq resolves via ENA (B2), GRCh38 is actually correct (B3), quick/cheap (B4). Run it only **after** the two smoke sbatches pass (compute-node curl; module/conda in a batch shell).

**Do NOT** fire the intended "mice" cohort first — it hits B3 (wrong genome) and, if internal, B2 (blank fastq).

**Open questions for the operator:** (1) Is the first cohort human or mouse? (2) Does it carry resolvable ENA/SRA accessions or is it internal? (3) `normal`'s default/max walltime? (4) Do compute nodes have outbound HTTPS/S3?
