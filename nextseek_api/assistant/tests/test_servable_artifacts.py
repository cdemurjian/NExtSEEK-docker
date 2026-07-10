from nextseek_api.services.assistant import _servable_artifacts


def test_only_in_root_files_are_advertised(tmp_path, monkeypatch):
    # tmp_path becomes an allowed artifact root via NEXTSEEK_OUTPUTS_DIR.
    monkeypatch.setenv("NEXTSEEK_OUTPUTS_DIR", str(tmp_path))
    good = tmp_path / "submission.xlsx"
    good.write_text("x")
    good2 = tmp_path / "wb2.xlsx"
    good2.write_text("y")

    saved = {
        "workbook": str(good),                                   # in-root file  -> advertised
        "geo_seq_workbooks": [str(good2)],                       # list of in-root paths -> advertised
        "staged_samplesheet": "/data/luria/run/samplesheet.csv", # out-of-root    -> dropped
        "tower_dataset_csv": "https://tower.example/datasets/ab",  # a URL, not a path -> dropped
    }
    arts = _servable_artifacts(saved, "/base")

    assert {a["key"] for a in arts} == {"workbook", "geo_seq_workbooks"}
    urls = {a["key"]: a["url"] for a in arts}
    assert urls["workbook"] == "/base/workbook/"
    assert urls["geo_seq_workbooks"] == "/base/geo_seq_workbooks/"


def test_empty_or_none_saved_files():
    assert _servable_artifacts(None, "/base") == []
    assert _servable_artifacts({}, "/base") == []
