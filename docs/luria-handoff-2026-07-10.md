# LURIA launch backend — E2E TEST HANDOFF (2026-07-10)

Self-contained resume + operations doc. Continues `docs/luria-handoff-2026-07-09.md` (design/build) and
`docs/luria-preflight-review.md`. Covers the **end-to-end live-testing marathon**: every failure hit driving
real nf-core runs on Luria, the fix, exactly where we are, and **how to operate** (SSH, diagnostics, rebuild).
Memory: `.claude/.../memory/project_luria_launch_mode.md` + `reference_nxf_syntax_parser.md`.

---

## 0. TL;DR — where we are

The LURIA backend is **built, committed, and driven end-to-end against real Luria.** 🎉 **First FULL run
completed 2026-07-10:** nf-core/scrnaseq 2.7.1 seqwell (*Macaca fascicularis*, Mfas6.0, alevin) → quant on
bcc, **11m30s, 45 succeeded, no errors** (verified outputs: per-sample af_quant + h5ad/rds matrices, AlevinQC,
MultiQC). The whole gauntlet (routing → parser → TLS → partition → nextflow version → fastq source → aligner
→ genome resolution → cell-calling) is cleared. All code fixes committed on `v3-full-integration`.

**The first green run used MANUAL run.sh edits; those are now PRODUCTIONIZED (design B, committed).** So a
**fresh chat-UI seqwell run should now go green hands-free** — that's the immediate thing to confirm after a
rebuild (§7). The mouse rnaseq cohort still needs local fastq staging.

**Most repeated lesson:** *match versions to the pinned era* (Nextflow ↔ pipeline; curated JSON vocab ↔
`catalog.py` revision), and *a `-c` ext.args override REPLACES, never appends* (§2.8, §8).

---

## 1. The two live test cohorts

| Cohort | Pipeline (pinned) | Samples | Genome | Status |
|---|---|---|---|---|
| **NHP scRNAseq (seqwell)** | nf-core/scrnaseq **2.7.1** | 6× `D.SEQ-220823SHA-*` (SRR18609420–425), *M. fascicularis* seqwell | **Mfas6.0** | ✅ **ran to completion** (manual); design B should reproduce hands-free after rebuild |
| **Mouse rnaseq** | nf-core/rnaseq **3.18.0** | 4× `D.SEQ-240910LAU-*` (SRR10085138/180/181/182) | GRCm39 | blocked on fastq source — needs its fastqs staged locally + `Link_*` metadata (like NHP) |

- **NHP fastqs staged:** `/net/bmc-pub10/data1/bmc/pipeline_cd/files/fastq/SRR186094{20..25}_{1,2}.fastq.gz`,
  and NExtSEEK metadata has **`Link_PrimaryData`/`Link_SecondaryData`** = those local paths.
- **Mouse fastqs NOT staged** — that cohort still ships ENA URLs → fails compute-node URL validation (§2.6).
- Pins live in `chat_nextseek/.../seqera/catalog.py` (`default_revision`): scrnaseq **2.7.1**, rnaseq **3.18.0**.

---

## 2. The gauntlet — every failure + fix (order hit)

**2.1 Routing (dmac-assistant Container-CC).** Build query → NS (chat_nextseek pipeline_agent builds + pauses
"confirm?"), but the follow-up ("Confirm"/"Submit to Luria") re-routed to **Container-CC** (fresh ephemeral
`claude` container, own session) → no knowledge of the paused pipeline. Root cause: the BAML router
(`nextseek_api/cc_assistant/router.py::decide` → `dmac_assistant` `RouteQuery`) is **stateless** —
`RouterInput={user_query,routes}`, NO history. Bare follow-ups carry no pipeline signal → dumped to CC. (The
keyword `_heuristic` would've defaulted them to NS — contextless BAML is *worse* than the fallback.) CC
transcripts prove it: `/dmac/users/3-mit-srp/cdemurjian/output/raw/transcript-*.jsonl`. Route decision is an
SSE `route_decided` event (not a log line). **Interim workaround (parallel agent, 9f48942):** admin-only
route-override toggle (BAML/C_NS/CC) — force NS. **Real fix (NOT done):** router memory / shared-memory (§6).

**2.2 Nextflow config parser.** `nextflow.config:311: Variable declarations cannot be mixed with config
statements` — Nextflow 26.04.4's strict parser rejects rnaseq 3.18.0's legacy config. **Band-aid:** `export
NXF_SYNTAX_PARSER=v1` in run.sh. **Superseded by 2.5** (the version downgrade), but left in (harmless on 24.x).

**2.3 Singularity TLS (compute-node CA).** `x509: certificate signed by unknown authority` pulling
`depot.galaxyproject.org`. Root cause (PROVEN on node c14 via sbatch test): **compute nodes ship a CA bundle
MISSING ISRG Root X1** (Let's Encrypt root). curl to S3/quay worked in preflight because those use other CAs.
**Fix:** staged the login node's current bundle → `pipeline_cd/certs/ca-bundle.crt` (has ISRG); run.sh now
`export SSL_CERT_FILE="$(dirname {{SINGULARITY_CACHE}})/certs/ca-bundle.crt"`. Verified B_OK on c14. **Refresh
the bundle by re-copying login `/etc/pki/tls/certs/ca-bundle.crt`** if roots change. (6802eca)

**2.4 SLURM partition.** Jobs pended `(Resources)` forever: run.sh had **no `#SBATCH -p`** → default partition
`normal` (16-core/96GB nodes, all busy — a `-n16` req needs a whole free node). `run_script.py` defaulted
`partition="bcc"` but it wasn't wired in. **Fix:** `#SBATCH -p {{PARTITION}}` (→ **bcc** = cdemu's ki-bcc home
partition: idle nodes + **384GB** RAM → schedules instantly, kills STAR-OOM risk). `kellis` (28d, 96-core,
384GB+, ~900 idle cores) is the overflow. All 3 partitions AllowGroups=ALL. (6802eca)

**2.5 Nextflow version (the big one).** After 2.2, the run reached `PREPARE_GENOME:GUNZIP_FASTA` → exit 126,
`.command.run: Permission denied`. Root cause: **Nextflow 26.04.4 generated a MALFORMED singularity
`nxf_launch`** — `process.shell`'s `set -e/-u/-o pipefail` got injected between `bash` and the `.command.run`
path, so it tried to *directly execute* the 644 (non-+x) wrapper. Second 26.04.4↔nf-core-3.18.0 incompat.
**DURABLE FIX: downgraded `cdemu_nfcore` to Nextflow 24.10.6** (`conda install -n cdemu_nfcore -c bioconda -c
conda-forge 'nextflow>=24.10,<25'`) — the nf-core era ("Oct24"=24.10, oatwa's env lineage). Verified: exactly
`nextflow 24.10.6`, no 26.x, no `NXF_VER`, no stale framework cache. **Luria-side env change, no rebuild.**
See `[[nxf-syntax-parser-v1-nfcore]]`.

**2.6 Samplesheet fastq source.** Under correct nf 24.10.6, samplesheet validation FAILS on the emitter's
**ENA https URLs**: nf-core schema is `format:file-path` + `exists:true`; the compute node rejects remote URLs
(curl / `file(url).exists()` work on the LOGIN node, but the pipeline's compute-node validation fails). Passed
under nf26 only because the v1 band-aid loosened validation. **Fix = local fastqs via metadata (option A):**
  - Emitter was **100% ENA-synthesized** and never got the sample metadata. Threaded it: `resolve_samples`
    stashes each leaf's FULL metadata per accession → `state["accession_file_paths"]`; `write_samplesheet`
    passes it as `accession_metadata`; emitter picks fastq paths from it (ENA fallback).
  - **Field-agnostic picking:** the path is NOT in `File_PrimaryData` (that holds the bare accession `SRR…`);
    it's in **`Link_PrimaryData`/`Link_SecondaryData`**, any name. `emitter._fastq_from_meta(meta,'primary'|
    'secondary')` scans EVERY field for a value that IS a fastq path (`/…/*.fastq.gz`), assigns R1/R2 by
    field-name hint (primary/secondary, r1/r2) **or** the `_1`/`_2` filename marker, skips accession/checksum
    fields, prefers local over URL. Also fixed CRLF (`csv.DictWriter` default `\r\n` → `lineterminator="\n"`).
    (6802eca option-A+CRLF; cbfef44 field-agnostic + full-metadata thread)
  - **Open caveat:** `if not runs: continue` still requires the accession to ENA-resolve (drives run/layout
    enrichment) — fine for these SRR cohorts; a purely-local sample with no accession would be dropped.

**2.7 scRNAseq aligner version mismatch.** `--aligner: 'simpleaf' is not a valid choice`. `scrnaseq.json` was
curated against **latest** docs (`simpleaf`), but `catalog.py` pins **2.7.1** where the alevin-fry backend is
still named **`alevin`** (renamed to simpleaf in 3.x). **Fix:** aligner enum → 2.7.1's set (`alevin` default),
protocol enum trimmed (removed the simpleaf geometry string; `dropseq` stays), presets → alevin. The seqwell
curation itself (protocol dropseq, genome Mfas6.0, species resolution) was all correct. (cbfef44)

**2.8 Genome resolution + seqwell cell-calling — MANUAL then PRODUCTIONIZED (design B).**
  - **Genome:** `--genome Mfas6.0 -c luria.config` does NOT resolve fasta/gtf — `params.genomes` map lookup is
    broken in Nextflow (`getGenomeAttribute` returns the value but the workflow assertion sees empty). Fix:
    explicit **`--fasta/--gtf`** on the CLI. Got past indexing.
  - **Cell-calling:** `SIMPLEAF_QUANT` needs one of `--knee|--unfiltered-pl|--forced-cells|--expect-cells`
    (`modules/local/simpleaf_quant.nf`), from `meta.expected_cells` or `ext.args`. Unfiltered fallback needs a
    barcode whitelist → seqwell/dropseq has none → error. No top-level param. Fix: **`--knee`** (whitelist-free
    knee-point) via a `-c` process config.
  - **`-r cr-like` clobber (54872eb):** `SIMPLEAF_QUANT`'s 2.7.1 pipeline default is `ext.args = "-r cr-like"`
    (the `--resolution`, in the `aligner=="alevin"` config block). A `-c` override **REPLACES** it (Nextflow
    can't append — VERIFIED: self-ref closure → `StackOverflowError`, string → clobber), so a `--knee`-only
    override dropped `--resolution` → crash. **The override must carry the base:** `-r cr-like --knee`.
  - **PRODUCTIONIZED (design B, c1a84dc + 54872eb):** `submit_to_luria` now injects both automatically —
    - `--fasta/--gtf` from the new `LURIA_GENOMES` registry (single source for the map + the flags; path >
      iGenomes, GLOBAL/all pipelines); `render_run_script(refs_root=…)`. Deleted `luria.config.tmpl`.
    - `SIMPLEAF_QUANT "-r cr-like --knee"` declared in `scrnaseq.json` `protocol_process_args` (data-driven) →
      `pipeline_params.process_args_for()` → `tool_submit_to_luria` → `run_script.render_process_config()`.
    - `render_process_config` REFUSES `STAR_ALIGN` (its `ext.args` default is non-empty → a `-c` override
      would clobber `--twopassMode`/`--outSAMtype BAM`/…). **STAR needs no override** — it tunes cell-calling
      via samplesheet `expected_cells` + STAR's default knee `--soloCellFilter`. To run the STAR comparison,
      steer **`aligner: star`** in the query. Aligner scope: seqwell runs on alevin/kallisto/STARsolo; only the
      10x-only cellranger* family is excluded. alevin = fast pseudoalign/no-BAM; STAR = heavy, BAMs, velocity.

---

## 3. Commits this session (`v3-full-integration`)

| SHA | What |
|---|---|
| `3bce827` | [parallel] checkpoint: seqwell scrnaseq params, luria.config.tmpl, reference_bundles Mfas6.0, submitter 4-file staging, pipeline_params gencode, rnaseq save_trimmed, NXF_SYNTAX_PARSER |
| `9f48942` | [parallel] admin-only route override (BAML/C_NS/CC) toggle |
| `6802eca` | SSL_CERT_FILE CA bundle + `-p bcc` partition; option-A emitter (File_*→fastq, ENA fallback) + LF samplesheets |
| `cbfef44` | emitter picks fastq by value (field-agnostic, local>URL); scrnaseq.json aligner→alevin (pinned 2.7.1) |
| `c1a84dc` | **design B:** `--fasta/--gtf` from LURIA_GENOMES registry (deleted luria.config.tmpl) + per-protocol process ext.args (dropseq→SIMPLEAF_QUANT, data-driven from scrnaseq.json) |
| `54872eb` | SIMPLEAF_QUANT keeps base `-r cr-like` + `--knee` (override replaces); guard render_process_config vs clobbering STAR_ALIGN |

**⚠️ Working tree also has UNRELATED, NOT-OURS changes:** `startup/seed/*.sql.gz` deletions (+ `regenerate/`)
and a `static/js/chat_assistant/` manifest change — **leave them** (the seed blobs are the PII landmine from
the parallel v3 review). **Only ever `git add` explicit `chat_nextseek/` paths.** Nothing pushed (local only).

---

## 4. OPERATING ON LURIA (SSH, diagnostics, recipes)

**The agent has no direct Luria access — it works through the `nextseek` docker container**, which has the
Luria key bind-mounted (same host↔container path, RO) and a persisted known_hosts volume. So *every* Luria
action is `docker exec nextseek bash -c '… ssh …'`.

**Key + host facts:**
- Key: `/home/cdemu/code/keys/luria` (RO bind mount). SSH needs mode 600 → **copy to /tmp + chmod first.**
- known_hosts: `/app/.ssh/known_hosts` (volume `nextseek-luria-ssh`, persisted).
- Host/user: `cdemu@luria.mit.edu`. **Key-only, NO Duo.**

**Canonical read-only invocation:**
```bash
docker exec nextseek bash -c '
cp /home/cdemu/code/keys/luria /tmp/lk && chmod 600 /tmp/lk
ssh -i /tmp/lk -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile=/app/.ssh/known_hosts -o ConnectTimeout=25 \
    cdemu@luria.mit.edu "<READ-ONLY CMD>"
rm -f /tmp/lk'
```

**⭐ THE KEY TECHNIQUE — pipe scripts over stdin.** Inline quoted remote commands break CONSTANTLY on `;`,
`'`, `()`, `%`, backticks (nested quoting through docker+ssh). For anything non-trivial, write a script to the
scratchpad, `docker cp` it in, and run `ssh … bash -s < /tmp/x.sh`:
```bash
cat > /tmp/claude-.../scratchpad/x.sh <<'EOF'
# remote commands here — any quoting, freely
ls -la /net/bmc-pub10/data1/bmc/pipeline_cd/runs/
EOF
docker cp /tmp/claude-.../scratchpad/x.sh nextseek:/tmp/x.sh
docker exec nextseek bash -c '
cp /home/cdemu/code/keys/luria /tmp/lk && chmod 600 /tmp/lk
ssh -i /tmp/lk -o BatchMode=yes -o UserKnownHostsFile=/app/.ssh/known_hosts -o ConnectTimeout=30 \
    cdemu@luria.mit.edu bash -s < /tmp/x.sh
rm -f /tmp/lk /tmp/x.sh'
```

**Loading the nf env (conda/nextflow/singularity)** — the remote script must do this itself (a login shell
alone isn't enough), and do NOT pipe `module`/`conda` through `head`/subshells (env changes get lost):
```bash
module add miniconda3/v4
source /home/software/conda/miniconda3/etc/profile.d/conda.sh
conda activate cdemu_nfcore          # -> nextflow 24.10.6
module add singularity/3.10.4
```

**Staging files (scp):**
```bash
scp -i /tmp/lk -o BatchMode=yes -o UserKnownHostsFile=/app/.ssh/known_hosts <local> cdemu@luria.mit.edu:<remote>
```

**Gotchas learned this session:**
- Foreground `sleep` is blocked in the agent's Bash tool → poll with a `for i in $(seq…); do …; sleep 5; done`
  loop *inside the remote script*, or use `run_in_background`.
- Big/slow ops (downloads, `conda search`) → run `nohup … &` on Luria and `tail` the log, or Bash background.
- A poll can race the scheduler (a just-submitted job's `.out` doesn't exist yet) — re-check, don't assume.

**Diagnostic recipes (all via the stdin-pipe pattern):**
- **Inspect a run dir** `RD=…/runs/<name>`: `squeue -u cdemu` + `sacct -j <id> --format=…State,ExitCode,NodeList,Elapsed`;
  `grep -nE 'nextflow run|--genome|--fasta|--gtf|-c |SBATCH -p' $RD/run.sh`; `cat $RD/params.yml $RD/samplesheet.csv`;
  `tail -50 $RD/*.out`; `grep -aiE 'ERROR|Caused by|exit status|Command error|Permission|OutOfMemory|process >' $RD/.nextflow.log | tail`.
- **Read a pipeline's contract** (the local clone at `~/.nextflow/assets/nf-core/<pipeline>/`):
  `nextflow_schema.json` (param enums/defaults — e.g. aligner), `assets/schema_input.json` (samplesheet
  columns + `format:file-path`/`exists`), `conf/modules.config` (**process `ext.args` defaults** — critical for
  overrides), `modules/local/*.nf` (how a process builds its command). This is how we found aligner=`alevin`,
  the URL-`exists` gate, and SIMPLEAF_QUANT's `-r cr-like` default.
- **Prove a Nextflow config behavior** — write a tiny `main.nf` + `base.config`/`over.config`, `NXF_SYNTAX_PARSER=v1
  nextflow run main.nf -c … -c …`, grep the echoed value. (This is how we proved ext.args can't append:
  closure→StackOverflow, string→clobber.) Local executor, no container → fast.
- **Partition/resources:** `sinfo -s`; `sinfo -o "%R|%a|%D|%t|%C|%m|%z"` (avoid `%P` with width — it mangles);
  `squeue -u cdemu -o "%.12i %.10P %.9T %R"`. `scontrol show partition bcc`.
- **CA bundle (TLS):** `grep -c 'ISRG Root X1' <bundle>` (RHEL bundles carry the friendly-name comment; the
  login bundle has it, compute nodes don't). Confirm a compute node via a tiny `sbatch` that does
  `singularity pull` with/without `SSL_CERT_FILE`.
- **URL reachability:** `curl -sI <url>` (system CA) + a tiny `nextflow` with `file('<url>').exists()`
  (Java/Nextflow's own check — differs from curl).
- **conda env sanity:** `conda list -n cdemu_nfcore nextflow` (exactly one, 24.10.6) · `echo $NXF_VER` (unset) ·
  `which -a nextflow` (no shadowing) · `nextflow -version`.

---

## 5. Luria environment (current, validated)

- **conda env `cdemu_nfcore`: Nextflow 24.10.6** (downgraded from 26.04.4 — CRITICAL), singularity 3.10.4.
- **`LURIA_WORKING_PATH=/net/bmc-pub10/data1/bmc/pipeline_cd`** (31 TB, cdemu-writable). Subdirs:
  `runs/` (per-run dirs) · `work/` (nextflow work) · `singularity_cache/` · `refs/` (genomes + `download_refs.sh`)
  · `certs/ca-bundle.crt` (CA w/ ISRG) · `files/fastq/` (staged fastqs).
- **Refs** (all `gzip -t` OK): GRCh38+GRCm39 (GENCODE v46/M39), Mfas6.0 + Mmul_10 (Ensembl r116 toplevel).
  Registered in `LURIA_GENOMES` (run_script.py) → drives `luria.config` map + `--fasta/--gtf`.
- **Partitions** (AllowGroups=ALL): **bcc** (home, 384GB, idle — DEFAULT now) · `normal` (busy cluster default)
  · `kellis` (28d, big, overflow). Nodes have internet egress; compute nodes lack ISRG root (§2.3).
- **SLURM:** `normal`/`bcc` MaxTime 14d, `kellis` 28d. `sbatch` present; local-executor runs (nextflow runs
  ALL processes on the one `-N1 -n{CPUS=16}` node — not the slurm executor).
- **Per-run staged files** (submitter scps 4): `run.sh`, `luria.config` (genomes map + any process block),
  `params.yml`, `samplesheet.csv`. run.sh cd's into the run dir, so `-c luria.config`/`-params-file params.yml`
  are relative.

---

## 6. Open items / next steps

**IMMEDIATE — validate design B (the real test):** rebuild, then a **fresh chat-UI seqwell run should complete
HANDS-FREE** (submitter emits `--fasta/--gtf` + `luria.config` with `SIMPLEAF_QUANT { ext.args='-r cr-like
--knee' }`, no manual edits). Also try **`aligner: star`** for the STAR comparison (no `--knee`/config needed).
Force NS via the admin route toggle.

**THEN — mouse rnaseq E2E:** stage its 4 fastqs locally + set `Link_PrimaryData/Link_SecondaryData` (like NHP),
then re-run. Otherwise it fails compute-node URL validation.

**BACKEND FOLLOW-UPS:**
- **Router memory / shared-memory API (real fix for §2.1).** A thin, injectable **canonical conversation/
  event-log** shared by all 3 brains (top-level BAML router has none; chat_nextseek + Container-CC each have
  their own). Layer it: shared thin log (recent_turns + active-flow flags + turn summaries) all 3 publish to +
  read; each keeps its private rich memory. Router = first consumer (fixes mis-route generically). Phase 1 =
  shared log + router reads it; phase 2 = NS+CC publish/consume. Worth a brainstorm→spec→plan. Admin toggle
  (9f48942) is the interim. (BAML `RouterInput` has no history field; `decide(query)` gets only the bare string.)
- **Version-drift guard:** curated JSON param vocab must match the pinned `catalog.py` revision (bit us twice:
  nextflow-vs-pipeline, scrnaseq simpleaf-vs-alevin). A CI check flagging JSON↔pinned-revision drift. Consider
  bumping scrnaseq off 2.7.1 later (restores simpleaf + geometry string; needs nf-24.10 compat check).
- **Prebuild indices** (STAR/salmon/alevin) → register in `LURIA_GENOMES`/luria.config (speed + kills OOM;
  add `--star_index`/`--salmon_index` to run.sh like `--fasta/--gtf`).
- **fetchngs-style local fastq staging** as a first-class submit step (so users don't hand-download +
  hand-edit `Link_*` metadata). The `if not runs: continue` ENA gate + tower_complete gate on launch.yml still open.

---

## 7. Resume sequence

1. **Rebuild** (ships all design-B commits): `cd /home/cdemu/code/dmac/docker/v3 && docker compose up -d --build nextseek` (~2min; user runs it per [[feedback_docker_control]]).
2. **Fresh chat-UI seqwell query** (force NS via toggle) → should complete **hands-free**. Then `aligner: star`
   for the STAR comparison.
3. **If a new error appears:** SSH the run dir (§4 recipe) — confirm `run.sh` has `--fasta/--gtf`, `luria.config`
   has the `SIMPLEAF_QUANT '-r cr-like --knee'` block, `params.yml` aligner, `samplesheet.csv` has local `Link_*`
   paths; then `.out`/`.err`/`.nextflow.log`. Read the pipeline's `conf/modules.config`/module before any
   ext.args override (check the base default).
4. **Tests:** `cd chat_nextseek && uv run pytest tests/ -k "luria or pipeline or emitter or params" --ignore=tests/evaluator -q`.
5. **Commit:** explicit-stage ONLY `chat_nextseek/` paths; end message with `Co-Authored-By: Claude Opus 4.8 (1M context)`.
6. Then mouse-rnaseq staging (§6), and the router-memory design.

---

## 8. Key lessons (so we don't relearn them)

1. **Match versions to the pinned era.** Nextflow 24.10.6 (not 26.x) for nf-core-3.18.0/2.7.1; curated JSON
   param vocab (aligner names, protocol enums) to the pinned `catalog.py` revision. Two separate failures.
2. **A `-c` `ext.args` override REPLACES, never appends** (Nextflow — verified). Read the pipeline's
   `conf/modules.config` default FIRST; carry the base + your addition (`-r cr-like --knee`), or don't override
   (STAR → use samplesheet `expected_cells`). `render_process_config` guards known non-empty-default processes.
3. **Compute nodes ≠ login node** for TLS (missing ISRG root) and remote-URL validation. Anything the run needs
   from the internet must be reachable/verifiable *from a compute node* (or staged locally). Prefer LOCAL
   fastqs + `SSL_CERT_FILE` over relying on compute-node external access.
4. **path > iGenomes on Luria.** The `params.genomes` map (`--genome key`) doesn't resolve reliably; pass
   explicit `--fasta/--gtf` from the registry. Global across pipelines.
5. **Pipe remote scripts over stdin** (§4) — inline quoting through docker+ssh is a trap.
6. **Only stage `chat_nextseek/` in commits** — the working tree carries unrelated PII-landmine seed blobs.
7. **The router splits multi-turn flows** — until shared memory lands, force NS with the admin toggle for any
   pipeline build→confirm→submit conversation.

---

## 9. STAR/BAM path for whitelist-less seqwell (Phase 1, 2026-07-10)

Alevin gives count matrices but no genomic BAMs. To get BAMs (velocity, browser tracks, re-quant, variants)
we added a **STARsolo** path beside the alevin path. Spec + plan:
`docs/superpowers/{specs,plans}/2026-07-10-luria-starsolo-dropseq-*`. Code: commits `869a088`, `5f5b157`,
`e122e23` on v3-full-integration.

**Why a vendored clone (not config).** nf-core/scrnaseq 2.7.1 does not support whitelist-less bead protocols
for STARsolo: `modules/local/star_align.nf` hardcodes `--soloCBwhitelist <(gzip -cdf $whitelist)` (Drop-seq
has no whitelist, so it is empty and STAR fatals), and `assets/protocols.json` `star.dropseq` supplies no
CB/UMI geometry. Both are in the pipeline code, not reachable by `-c`/param. A version bump does not help
(4.2.0 still requires a whitelist). So we clone scrnaseq@2.7.1 to a pinned Luria path and patch it.

**The clone.** `{LURIA_WORKING_PATH}/pipelines/scrnaseq-2.7.1-star-patched`, provisioned idempotently by
`chat_nextseek/src/chat_nextseek/luria/pipelines/scrnaseq_2_7_1_star/provision.sh` (also holds
`run_star_validation.sh` + README). Three edits, all in the clone:
1. `protocols.json` `star.dropseq.extra_args` = `--soloCBstart 1 --soloCBlen 12 --soloUMIstart 13 --soloUMIlen 8`
   (seqwell 12bp CB + 8bp UMI, via the same `extra_args` channel 10x uses for `--soloUMIlen`). `getProtocol`
   returns it as `other_10x_parameters`, injected into the STAR command.
2. `star_align.nf`: `def whitelist_arg = whitelist ? "--soloCBwhitelist <(gzip -cdf ${whitelist})" :
   "--soloCBwhitelist None"` (bead barcode discovery + knee `soloCellFilter`; 10x path unchanged).
3. `--soloBarcodeReadLength 0` appended to the geometry: these R1 reads are **21bp**, not 20, and STARsolo
   strict-checks barcode-read length == CB+UMI. This only tolerates the extra base (CB stays 1-12, UMI 13-20),
   matching what salmon alevin does leniently. Found from STAR's own stderr, not guessed.

**Run.** Manual (Phase 1): a `run.sh` does `nextflow run <clone>` (NO `-r`), `aligner: star`, `protocol:
dropseq`, `--fasta/--gtf` Mfas6.0, own run dir + `-w nfcore_scrnaseq_star`, reusing the green alevin
samplesheet. Job `11196484`, `runs/nfcore_scrnaseq_star_260710_162129_0`. Result: **5/6 samples green and
correct** (38550 genes x 719-2795 cells per sample, sane; coordinate-sorted BAMs), SHA-1 (SRR18609425, 200M+
reads, very deep) finishing at last check. The `.command.sh` in the work dir confirmed `--soloCBwhitelist
None` + the 12/8 geometry + `--soloBarcodeReadLength 0` threaded. No errors.

**Isolation (alevin untouched).** Alevin runs stock `nf-core/scrnaseq -r 2.7.1` (byte-identical); only
`aligner=star` uses the clone; `STAR_ALIGN` is not in alevin's DAG; the 76 existing tests are unchanged
(green). Alevin needs no patch because salmon `--protocol dropseq` carries the geometry and does knee
cell-calling internally. This is the "per-aligner source" isolation.

**Open: velocity.** The BAMs have NO `CB`/`UB` tags (STAR default `outSAMattributes` = `NH HI AS nM`), and
`--soloFeatures` is `Gene` only. RNA velocity needs one of: `--outSAMattributes NH HI AS nM CB UB` (CB/UB in
the BAM for velocyto-on-BAM) and/or `--soloFeatures Gene Velocyto` (spliced/unspliced matrices for scVelo).
Each is a one-line clone tweak + a STAR_ALIGN re-run. **Decision pending with user.**

**Phase 2 (BUILT, commit `7df29bc`, TDD, 84 tests): the LLM path productionized.** A general
`LURIA_VENDORED_PIPELINES` registry keyed `(pipeline, aligner)` -> clone path (shaped like `LURIA_GENOMES`, in
`run_script.py`) + `resolve_pipeline_source(pipeline, aligner, revision, working)` that `submit_to_luria` calls
before render: for a registered entry it sets `{{PIPELINE}}` to the clone path and drops `-r` (a `-r <tag>` on
a local clone would `git checkout` the tag and **wipe the patches**); `run.sh.tmpl` uses a `{{REVISION_FLAG}}`
token (`-r 2.7.1` for stock, empty for a clone). The pipeline name is normalized so the
`https://github.com/nf-core/scrnaseq` form matches the `nf-core/scrnaseq` key. `scrnaseq.json` is unchanged
(geometry lives in the clone, not the LLM menu); the inert alevin-only `SIMPLEAF_QUANT` block under a star run
is harmless (optional future cleanup: make `process_args_for` aligner-aware). **NOT LIVE until a container
rebuild** (`cd docker/v3 && docker compose up -d --build nextseek`, user-run) because chat_nextseek is COPY'd
into the image. After the rebuild, a chat-UI `aligner: star` scrnaseq run launches STARsolo hands-free.

**Lesson.** STARsolo needs explicit bead **geometry + whitelist-None + barcodeReadLength**; alevin carries all
of that internally via the protocol name. When a STARsolo run fails, read the STAR `.command.err`/`Log.out` in
the work dir (exit 104 = input-file/barcode issue), and the `.command.sh` is the proof the patched flags
threaded.
