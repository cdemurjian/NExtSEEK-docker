from chat_nextseek.luria.run_script import (
    validate_resources,
    sanitize_job_name,
    render_run_script,
)


def test_validate_resources_defaults_when_empty():
    assert validate_resources({}) == {"partition": "bcc", "time": "48:00:00", "cpus": "2", "mem": "8G"}


def test_validate_resources_accepts_valid_overrides():
    out = validate_resources({"partition": "high", "time": "12:00:00", "cpus": 8, "mem": "64G"})
    assert out == {"partition": "high", "time": "12:00:00", "cpus": "8", "mem": "64G"}


def test_validate_resources_rejects_bad_values_falls_back():
    out = validate_resources({"partition": "bad; rm -rf /", "time": "lots", "cpus": 999, "mem": "8GB"})
    assert out == {"partition": "bcc", "time": "48:00:00", "cpus": "2", "mem": "8G"}


def test_validate_resources_rejects_trailing_newline():
    out = validate_resources({"partition": "bcc\n", "time": "12:00:00\n", "mem": "64G\n"})
    assert out == {"partition": "bcc", "time": "48:00:00", "cpus": "2", "mem": "8G"}


def test_validate_resources_non_dict_falls_back():
    assert validate_resources(["not", "a", "dict"]) == {"partition": "bcc", "time": "48:00:00", "cpus": "2", "mem": "8G"}


def test_sanitize_job_name_strips_shell_chars():
    assert sanitize_job_name("rnaseq; rm -rf /") == "rnaseq_rm_-rf"
    assert sanitize_job_name("") == "nfcore_run"


def test_render_substitutes_all_slots_and_no_tokens_left():
    script = render_run_script(
        job_name="nfcore_rnaseq", pipeline="nf-core/rnaseq", revision="3.21.0",
        work_dir="/net/x/work/nfcore_rnaseq", singularity_cache="/net/x/singularity_cache",
        resources={"partition": "bcc"},
    )
    assert "#SBATCH --job-name=nfcore_rnaseq" in script
    assert "#SBATCH --partition=bcc" in script
    assert "nextflow run nf-core/rnaseq" in script
    assert "-r 3.21.0" in script
    assert "-profile slurm,singularity" in script
    assert "-w /net/x/work/nfcore_rnaseq" in script
    assert "export NXF_SINGULARITY_CACHEDIR=/net/x/singularity_cache" in script
    assert "{{" not in script and "}}" not in script
