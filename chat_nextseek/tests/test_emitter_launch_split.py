from pathlib import Path
from chat_nextseek.seqera.emitter import emit_nfcore_artifacts, emit_launch_artifacts
from chat_nextseek.seqera.ena import ENAResolution, ENARun

TOWER = {"access_token": "t", "workspace": "w", "compute_env": "ce", "work_bucket": "/bucket"}


def _rows():
    return [{"sample": "S1", "accession": "SRR1", "strandedness": "auto"}]


def _resolutions():
    return [ENAResolution(accession="SRR1", runs=[ENARun(run_accession="SRR1", fastq_1="ftp://a_1.fq.gz",
            fastq_2="ftp://a_2.fq.gz", layout="PAIRED")], missing=False, reason="")]


def test_emit_nfcore_still_writes_params_and_launch(tmp_path):
    res = emit_nfcore_artifacts(
        tmp_path, pipeline="rnaseq", samplesheet_rows=_rows(), resolutions=_resolutions(),
        launch_plan={"run_name": "r1", "params": {"aligner": "hisat2"}}, tower_env=TOWER)
    assert (tmp_path / "samplesheet.csv").exists()
    assert (tmp_path / "params.yml").exists()
    assert (tmp_path / "launch.yml").exists()
    params_text = (tmp_path / "params.yml").read_text()
    assert "aligner" in params_text and "input" in params_text and "outdir" in params_text


def test_emit_prefers_local_fastq_paths_over_ena_and_writes_lf(tmp_path):
    # Option A: curated local paths (File_PrimaryData=R1, File_SecondaryData=R2) win over the
    # synthesized ENA URL; ENA is the fallback only when they're absent. Plus: LF line endings.
    rows = [{"sample": "S1", "accession": "SRR1", "strandedness": "auto"},
            {"sample": "S2", "accession": "SRR2", "strandedness": "auto"}]
    resolutions = [
        ENAResolution(accession="SRR1", missing=False, reason="", runs=[ENARun(
            run_accession="SRR1", fastq_1="ftp://ena1_1.fq.gz", fastq_2="ftp://ena1_2.fq.gz", layout="PAIRED")]),
        ENAResolution(accession="SRR2", missing=False, reason="", runs=[ENARun(
            run_accession="SRR2", fastq_1="ftp://ena2_1.fq.gz", fastq_2="ftp://ena2_2.fq.gz", layout="PAIRED")]),
    ]
    acc_meta = {"SRR1": {"File_PrimaryData": "/net/luria/S1_1.fastq.gz",
                         "File_SecondaryData": "/net/luria/S1_2.fastq.gz"}}  # SRR2 has no local path
    emit_nfcore_artifacts(tmp_path, pipeline="rnaseq", samplesheet_rows=rows,
                          resolutions=resolutions, accession_metadata=acc_meta,
                          launch_plan=None, tower_env={})
    sheet = (tmp_path / "samplesheet.csv").read_text()
    # SRR1: local paths used, ENA URL not present
    assert "/net/luria/S1_1.fastq.gz" in sheet and "/net/luria/S1_2.fastq.gz" in sheet
    assert "ftp://ena1_1.fq.gz" not in sheet
    # SRR2: no local path -> ENA fallback
    assert "ftp://ena2_1.fq.gz" in sheet
    # CRLF fix: plain LF, no stray carriage returns
    raw = (tmp_path / "samplesheet.csv").read_bytes()
    assert b"\r" not in raw


def test_emit_launch_artifacts_alone_writes_yamls(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,accession\nS1,SRR1\n", encoding="utf-8")
    res = emit_launch_artifacts(
        tmp_path, pipeline="rnaseq", samplesheet_path=sheet,
        launch_plan={"run_name": "r1", "params": {"aligner": "star_salmon", "genome": "GRCm39"}},
        tower_env=TOWER, excluded=[])
    assert (tmp_path / "params.yml").exists()
    assert (tmp_path / "launch.yml").exists()
    assert res.launch_entry is not None
    assert res.launch_entry["pipeline"] == "https://github.com/nf-core/rnaseq"
    params_text = (tmp_path / "params.yml").read_text()
    assert "star_salmon" in params_text and "GRCm39" in params_text


def test_emit_launch_does_not_inject_default_genome(tmp_path):
    # configure_run owns the genome decision via the bundle registry. The emitter must
    # NOT silently stamp the catalog's default_genome (human GRCh38 for rnaseq) when params
    # lack one — that produced a wrong-species genome on mouse data (verification finding).
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,accession\nS1,SRR1\n", encoding="utf-8")
    emit_launch_artifacts(
        tmp_path, pipeline="rnaseq", samplesheet_path=sheet,
        launch_plan={"run_name": "r1", "params": {"aligner": "star_salmon"}},  # no genome
        tower_env=TOWER, excluded=[])
    params_text = (tmp_path / "params.yml").read_text()
    assert "genome" not in params_text  # no default genome injected; configure_run is the only source
