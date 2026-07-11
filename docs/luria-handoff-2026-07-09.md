# LURIA launch backend — WORK HANDOFF (2026-07-09)

Self-contained resume doc. Read this + the three companion docs and you have everything.
Companion docs: `docs/superpowers/specs/2026-07-08-luria-launch-mode-design.md` (design),
`docs/superpowers/plans/2026-07-08-luria-launch-mode.md` (impl plan),
`docs/luria-preflight-review.md` (pre-flight review: blockers, checklist, failure playbook).
Memory: `.claude/.../memory/project_luria_launch_mode.md`.

---

## 0. TL;DR — where we are

We added a second pipeline **launch backend, LURIA**, to the chat_nextseek `pipeline_agent`: it builds the
same nf-core samplesheet, then `ssh`+`sbatch`es a generated `run.sh` on MIT's Luria SLURM cluster (alongside
the untouched Seqera/Tower backend). Code is done, reviewed, and its infra is **validated end-to-end against
real Luria**. A pre-flight review cleared 3 of 4 blockers; the 4th is dodged by a smart first-test choice.

**We are mid-way through two enhancements** the user asked for, and paused for a context reset:
- **NEXT STEP 1 — `luria.config`:** a Nextflow custom config with a `params.genomes` map (local fasta/gtf,
  later prebuilt indices), passed via `-c`, so `--genome <key>` uses LOCAL refs when available and falls back
  to iGenomes (S3 download) otherwise. **DESIGN CONFIRMED by user.** Reference fasta/gtf are **downloading now**.
- **NEXT STEP 2 — `scrnaseq.json` params:** add seqwell scRNAseq scientific params to the per-pipeline curated
  JSON, and un-hardcode the rnaseq-shaped flags currently baked into `run.sh`.

---

## 1. Repos, build, test, SSH — the operational basics

- **Code repo:** `/home/cdemu/code/dmac/docker/v3` — git branch **`v3-full-integration`**. `chat_nextseek/` is
  tracked inside it (NOT a separate repo; NOT bind-mounted — it's `COPY`'d into the `nextseek` image, so **code
  changes need a container rebuild to run live**).
- **Docs repo:** the OUTER `/home/cdemu/code/dmac/docker` — git branch **`feat/luria-launch-mode`** (spec + plan).
  This handoff + preflight-review live here in `docs/`.
- **chat_nextseek source:** `v3/chat_nextseek/src/chat_nextseek/`
- **Run unit tests:** `cd /home/cdemu/code/dmac/docker/v3/chat_nextseek && uv run pytest tests/<file> -q`
  (the `DJANGO_SETTINGS_MODULE` PytestConfigWarning is pre-existing noise; ignore it).
- **Rebuild the container** (to ship code changes for a live run):
  `cd /home/cdemu/code/dmac/docker/v3 && docker compose up -d --build nextseek` (~2 min; restarts only nextseek).
- **Commit code** to the v3 repo: `git -C /home/cdemu/code/dmac/docker/v3 add <paths> && git -C ... commit -m "..."`.
- **SSH-to-Luria pattern** (the pipeline agent runs IN the nextseek container and SSHes out; use this for any
  Luria diagnostic — READ-ONLY unless intentionally staging/downloading):
  ```bash
  docker exec nextseek bash -c '
  cp /home/cdemu/code/keys/luria /tmp/lk && chmod 600 /tmp/lk
  ssh -i /tmp/lk -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
      -o UserKnownHostsFile=/app/.ssh/known_hosts -o ConnectTimeout=20 \
      cdemu@luria.mit.edu "<READ-ONLY CMD>"
  rm -f /tmp/lk'
  ```
  For commands needing modules/conda, use `... cdemu@luria.mit.edu bash -lc "<CMD>"` (login shell). Do NOT pipe
  `module`/`conda` through `head` (runs them in a subshell → env changes lost).

---

## 2. Luria environment facts (all validated this session)

- **SSH:** `cdemu@luria.mit.edu`, key `/home/cdemu/code/keys/luria` (bind-mounted RO into the container at the
  SAME path). **Key-only, NO Duo.** known_hosts persisted in the `nextseek-luria-ssh` volume at `/app/.ssh`.
- **conda env:** `cdemu_nfcore` (in `~/.conda/envs`) — **Nextflow 26.04.4**. Created this session (nf-core_Oct24
  is oatwa's private env; the shared `nextflow` env is 22.10.4 = too old — don't use it).
- **Modules:** `module add miniconda3/v4`; conda init via `source /home/software/conda/miniconda3/etc/profile.d/conda.sh`;
  `module add singularity/3.10.4`.
- **Working path (`LURIA_WORKING_PATH`):** `/net/bmc-pub10/data1/bmc/pipeline_cd` — 31 TB free, writable by cdemu.
  Subdirs used by submit_luria: `runs/`, `work/`, `singularity_cache/`. New: `refs/` (downloads).
- **SLURM:** partition `normal` (Default=YES, AllowAccounts=ALL, **MaxTime=14 days**, DefaultTime=NONE → a bare
  job gets up to 14d). `sbatch` present. **Compute nodes HAVE internet egress** (verified: node c20 → S3=200,
  quay.io=401-reachable) → iGenomes download / singularity pulls / ENA fastq all work from nodes.
- cdemu groups: `ki-bmc`, `ki-bcc` (+ lab NFS). Can't read oatwa's `/home/oatwa/...` refs (perm denied) → that's
  WHY we're staging our own refs.

---

## 3. Config / env vars (all set in `v3/docker/nextseek.env` + `v3/.env`)

```
PIPELINE_LAUNCH_MODE=LURIA          # tower | luria; default preference for ambiguous "submit"
LURIAKEY=/home/cdemu/code/keys/luria   # host path == in-container path (same-path bind mount)
LURIA_USER=cdemu
LURIA_WORKING_PATH=/net/bmc-pub10/data1/bmc/pipeline_cd
# host luria.mit.edu is hardcoded in config.py's build_luria_env()
```
Both TOWER_* and LURIA_* are complete → the agent exposes BOTH `submit_to_tower` and `submit_to_luria`; a bare
"submit" defaults to `PIPELINE_LAUNCH_MODE` (=luria) via prompt injection. Say "submit to Luria" to be explicit.
`v3/.env` also has `LURIAKEY=` for docker-compose `${LURIAKEY:-/dev/null}` mount substitution. Startup templates
(`startup/templates/nextseek.env.template`, `startup/steps/config.py`) carry LURIA_* defaulted-off so a reset
doesn't drop them.

---

## 4. Architecture / end-to-end flow (file:function refs)

Request "run rnaseq on these mice, submit to Luria" →
1. `orchestrator.py::_handle_pipeline_agent_turn` gates on `pipeline_agent.is_active`; NFCORE → `pipeline_agent.start()`.
2. `pipeline/agent.py::_run_loop` (MAX_ITER=12). Tools built by `agent_tools.py::build_pipeline_tool_schemas(config)`:
   core (resolve_samples, write_samplesheet, configure_run) + `submit_to_tower` iff `TOWER_ENV_COMPLETE` +
   `submit_to_luria` iff `LURIA_ENV_COMPLETE` + conclude. `agent.py` injects `{launch_mode}`=`config.PIPELINE_LAUNCH_MODE`
   into the prompt (~agent.py:118).
3. `resolve_samples` → `write_samplesheet` (sets `state["artifacts"]["samplesheet"]` = real local path) →
   `configure_run` (`tool_configure_run` in agent_tools.py) → `seqera/emitter.py::emit_launch_artifacts` writes
   params.yml + launch.yml; sets `state["artifacts"]["launch"]` and `state["launch_plan"]` (= `plan.model_dump()`,
   whose `.params` = the merged curated params incl. the resolved `genome` key).
4. `agent_tools.py::tool_submit_to_luria` (~agent_tools.py:543): reads `state["artifacts"]["launch"]`,
   `["samplesheet"]`, and **`genome = state["launch_plan"]["params"]["genome"]`** (the B3 fix), calls
   `luria/submitter.py::submit_luria(launch, luria_env=config.LURIA_ENV, resources, job_name, samplesheet_local, genome)`.
5. `luria/submitter.py::submit_luria` → `_submit_one` per launch entry: resolve local samplesheet (from caller,
   fallback co-located `samplesheet.csv`), `run_genome = genome or "GRCh38"` (loud warning if defaulted),
   `render_run_script(...)`, `prepare_key` (600 temp copy), `ssh mkdir` runs/work dirs, `scp` run.sh +
   samplesheet.csv to `{WORKING_PATH}/runs/{name}_{id}/`, `ssh 'cd <dir> && sbatch run.sh'`, parse
   "Submitted batch job N". Returns `{job_id, remote_dir, log, run_name}`. Per-entry try/except; temp cleanup in finally.
6. `luria/run_script.py::render_run_script(*, job_name, pipeline, revision, run_dir, work_dir, singularity_cache,
   genome, resources)` substitutes `{{TOKEN}}`s in `luria/templates/run.sh.tmpl`. Validators fail-closed:
   `validate_revision`/`validate_pipeline`/`validate_genome` (allow-lists); `validate_resources` (only `cpus`
   reaches the template, default 16); `sanitize_job_name`.
7. run.sh on Luria: SBATCH `-N 1 -n {{CPUS}}`, mail cdemu@mit.edu, output/error in run dir; module miniconda3/v4;
   conda activate cdemu_nfcore; singularity/3.10.4; `export NXF_SINGULARITY_CACHEDIR={{SINGULARITY_CACHE}}`;
   `cd {{RUN_DIR}}`; `nextflow run {{PIPELINE}} -r {{REVISION}} -profile singularity --input samplesheet.csv
   --outdir . --genome {{GENOME}} --aligner star_salmon --save_reference --save_trimmed -w {{WORK_DIR}} -resume`.
   No `set -euo pipefail` (breaks `conda activate` under set -u — intentional). NO `-params-file` (CLI flags).

**Key files:** `luria/{submitter,run_script,ssh}.py`, `luria/templates/run.sh.tmpl`; `pipeline/{agent,agent_tools}.py`,
`orchestrator.py`; `config.py` (detect_pipeline_launch_mode / build_luria_env, ~line 13-40 + wiring ~197-235);
`seqera/emitter.py` (**tower_complete gate ~325-328** — only writes params/launch when all 4 TOWER_* set;
params input/outdir set to SEQERA_WORK_BUCKET `/orcd/...` ~416-417; fastq populated only for accession rows
~536-559); `prompts/pipeline_agent.txt`; per-pipeline curated params `reports/templates/nfcore/<key>.json`;
`v3/docker-compose.yml` (nextseek mount `${LURIAKEY:-/dev/null}` + `nextseek-luria-ssh` volume), `v3/Dockerfile`
(openssh-client).

---

## 5. Done this session (commits on `v3-full-integration`)

- Full `luria/` subpackage + `submit_to_luria` tool + per-config exposure + config + docker + startup templates
  (13 commits + review fixes — see the plan doc). Tower path byte-identical.
- `54cfdfc` — adapt run.sh to real Luria env (cdemu_nfcore, singularity 3.10.4, sbatch -N/-n, CLI flags, no params-file).
- `30e58e6` — **B3 fix**: thread species-resolved genome into `--genome {{GENOME}}` (was hardcoded GRCh38);
  + `validate_genome` allow-list. 66 luria tests pass.
- E2E infra validated; pre-flight review run (workflow) → `docs/luria-preflight-review.md`.

**Pre-flight blocker status:** B1 (compute-node egress) CLEARED · B3 (genome) FIXED · B4 (walltime, 14d) CLEARED ·
risk3 (batch-shell conda) CLEARED · **B2 (blank fastq for un-accessioned samples) OPEN** but sidestepped by
choosing a **2-sample human PUBLIC-ACCESSION** cohort as the first live test. Live submit is user-owned.

---

## 6. IN PROGRESS — reference downloads (verify first thing on resume!)

Started a background download on Luru (pid 8426) → `/net/bmc-pub10/data1/bmc/pipeline_cd/refs/`:
- Human GENCODE v46: `GRCh38.primary_assembly.genome.fa.gz` (845 MB, verified) + `gencode.v46.basic.annotation.gtf.gz` (30 MB, verified)
- Mouse GENCODE M39: `GRCm39.primary_assembly.genome.fa.gz` + `gencode.vM39.basic.annotation.gtf.gz` (M39 confirmed latest; exact filenames follow standard GENCODE naming — VERIFY they landed).

**FIRST THING ON RESUME — check the download** (via the SSH pattern in §1):
```
cat /net/bmc-pub10/data1/bmc/pipeline_cd/refs/refs_download.log
ls -la /net/bmc-pub10/data1/bmc/pipeline_cd/refs/
```
Expect `HUMAN_FASTA_OK / HUMAN_GTF_OK / MOUSE_FASTA_OK / MOUSE_GTF_OK / ALL_DONE`. If a MOUSE line says FAIL,
the M39 filename differs — list the dir `https://ftp.ebi.ac.uk/pub/databases/gencode/Gencode_mouse/release_M39/`
and re-fetch. These paths become the `luria.config` genomes-map values.

---

## 7. NEXT STEP 1 — `luria.config` (design CONFIRMED)

**Goal:** references via a Nextflow custom config's `params.genomes` map, passed with `-c`. `--genome <key>` then
uses LOCAL refs for keys in the map, else falls back to built-in iGenomes (S3). No branching in our code; the
B3 genome-key threading feeds it. Bonus: the map can also register **prebuilt STAR/salmon indices** (skip the
slow, OOM-prone index build) — do that as a follow-up.

**Build:**
1. Create a config template in the repo, e.g. `luria/context/luria.config` (or `luria/templates/luria.config`):
   ```groovy
   params {
     genomes {
       'GRCh38' {
         fasta = '/net/bmc-pub10/data1/bmc/pipeline_cd/refs/GRCh38.primary_assembly.genome.fa.gz'
         gtf   = '/net/bmc-pub10/data1/bmc/pipeline_cd/refs/gencode.v46.basic.annotation.gtf.gz'
       }
       'GRCm39' {
         fasta = '/net/bmc-pub10/data1/bmc/pipeline_cd/refs/GRCm39.primary_assembly.genome.fa.gz'
         gtf   = '/net/bmc-pub10/data1/bmc/pipeline_cd/refs/gencode.vM39.basic.annotation.gtf.gz'
       }
       // add: star_index / salmon_index paths once prebuilt; NHP (Mmul_10) once staged
     }
   }
   ```
   (Use the ACTUAL downloaded filenames from §6.) Consider templating the `pipeline_cd` prefix from
   `LURIA_WORKING_PATH` if generating in code.
2. **Stage it to Luria** at a fixed path, e.g. `/net/bmc-pub10/data1/bmc/pipeline_cd/luria.config` (scp once; or
   scp per-run into the run dir). A single fixed staged config is simplest; update it when refs/indices change.
3. **Wire `-c` into `luria/templates/run.sh.tmpl`** — add `-c {{NF_CONFIG}}` (or a fixed path) to the
   `nextflow run` line. Add a `NF_CONFIG` slot to `render_run_script`'s mapping + a config value/const for the
   staged config path (e.g. `{LURIA_WORKING_PATH}/luria.config`). Keep `--genome {{GENOME}}` as-is.
4. **Decision to make:** static staged config (simplest) vs. generated-from-repo-JSON at submit time (keeps the
   source of truth in git). Recommend: keep the config template in git, `scp` it to the fixed Luria path on submit
   (or on a `sync`), so git is source of truth and Luria always has the current one.
5. **`--gencode`:** GENCODE GTFs need `--gencode` for rnaseq — currently hardcoded in run.sh; when Step 2 moves
   pipeline flags to per-pipeline params, `--gencode` goes there (it's rnaseq-specific).
6. **Tests:** render test asserts the `-c <config>` appears and `--genome {{GENOME}}` still renders; a config
   presence/format test. Then `uv run pytest tests/test_luria_*.py -q`. Commit.
7. Rebuild the container; re-verify the rendered run.sh (submit_luria writes it to the run dir — can inspect via SSH).

---

## 8. NEXT STEP 2 — `scrnaseq.json` params (+ un-hardcode run.sh flags)

**Context:** per-pipeline curated scientific params live in `reports/templates/nfcore/<key>.json`
(`rnaseq.json`, `scrnaseq.json`, ...). `configure_run` → `seqera/pipeline_params.py::build_run_params(pipeline_key,
agent_params, bundle_key)` merges these curated defaults with agent overrides; the result lands in
`state["launch_plan"]["params"]`. Currently the Luria `run.sh` **hardcodes rnaseq-shaped flags**
(`--aligner star_salmon --save_reference --save_trimmed`, `--gencode`) — wrong for scrnaseq/other pipelines.

**Two parts:**
1. **Add seqwell scRNAseq params to `reports/templates/nfcore/scrnaseq.json`** (the user has specifics). nf-core/scrnaseq
   uses e.g. `--protocol` (seqwell is a supported protocol/preset), barcode/UMI/chemistry params, `--aligner`
   (alevin/star/cellranger), etc. Put the seqwell-specific curated defaults here. (Check the current scrnaseq.json
   shape + how rnaseq.json is structured first; mirror it.)
2. **Un-hardcode the pipeline flags in run.sh** so each pipeline gets ITS OWN params, not rnaseq's. Options:
   (a) reintroduce `-params-file params.yml` carrying the curated per-pipeline params, with CLI `--input
   samplesheet.csv --outdir .` overriding the bucket-shaped input/outdir the emitter writes (CLI overrides
   params-file in nextflow) — this reuses configure_run's curation and fixes the emitter's `/orcd` input/outdir
   at the same time; OR (b) thread a curated-flags string from `state["launch_plan"]["params"]` into a run.sh
   slot. Option (a) is cleaner and most nf-core-idiomatic. Either way, `--genome` still comes from the B3 thread /
   the luria.config genomes map, and `--gencode` becomes a per-pipeline param.
   - If reintroducing params.yml: `submit_luria` would stage params.yml again and add `-params-file params.yml`;
     re-add the input/outdir remap (or rely on CLI override). Update tests (they currently assert NO params.yml).

**Tie-in:** Steps 1 and 2 are complementary — Step 1 handles references (genome-agnostic, one config), Step 2
handles pipeline params (pipeline-specific, per-JSON). After both, run.sh is fully generic across pipelines +
uses local refs.

---

## 9. Open items / follow-ups / known gaps (from the pre-flight review)

- **B2 — blank fastq:** samplesheet `fastq_1/2` populated ONLY for accession-bearing rows (`emitter.py:536-559`);
  internal/un-accessioned NExtSEEK samples ship BLANK → nf-core rejects. No local-fastq→path mapping exists.
  Inspect the built samplesheet.csv before any submit; a mapping to NExtSEEK filestore fastqs is a bigger future task.
- **Emitter tower_complete gate:** Luria artifact emission piggybacks on Tower being configured (`emitter.py:328`).
  A Tower cred lapse would silently kill Luria submits. Latent; note if Tower is ever removed. Also every
  `configure_run` makes a live Tower dataset-upload API call even in Luria mode (side-effect).
- **fetchngs double-submit:** if `configure_run` `excluded_accessions != []`, a 2nd (fetchngs) launch.yml entry
  gets sbatch'd with the wrong sheet. Only proceed when excluded is empty; treat >1 launch entry as a stop.
- **Ambiguous "submit" routing is prompt-only** (no code tool_choice gate) — a mis-route could fire a live Tower
  job. Say "submit to Luria" explicitly; confirm the fired tool_use name.
- **No `--max_cpus/--max_memory`** → local-executor OOM risk on STAR index build. Prebuilt indices (Step 1 bonus)
  or resource caps mitigate.
- **`-resume` is a no-op across retries** (run_dir minted fresh each submit). Reuse the prior run_dir for a retry.
- **NHP (Mmul_10) refs** not staged (Ensembl, not GENCODE) — add to the download + genomes map later.
- Prebuild STAR/salmon indices once, register in luria.config → big speedup + kills OOM risk.

---

## 10. Recommended sequence on resume

1. **Verify the ref downloads** (§6) — the whole thing depends on the files landing.
2. **Build `luria.config`** (§7): template in repo → stage to Luria → wire `-c` into run.sh + render slot → tests → commit → rebuild.
3. **Add seqwell params to `scrnaseq.json` + un-hardcode run.sh flags** (§8) → tests → commit → rebuild.
4. Then the user's first live test: a **2-sample human public-accession** rnaseq cohort — "run rnaseq on
   <2 SRR/ERR ids>, submit to Luria" — watch `squeue -u cdemu` + `{run_dir}/{job}.out/.err/.nextflow.log`
   (failure playbook in `docs/luria-preflight-review.md` §5). This is user-owned.
