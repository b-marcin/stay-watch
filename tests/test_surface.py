from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_check_public_catches_an_inflected_name_nbsp_and_mixed_case(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "inflected.txt").write_text("pokoje w Exampletownach\n", encoding="utf-8")
    (docs / "nbsp.txt").write_text("at Example\u00a0Hotel today\n", encoding="utf-8")
    (docs / "case.txt").write_text("eXaMpLeToWnAcH\n", encoding="utf-8")
    from stay_watch.guard import check_public

    findings = check_public(tmp_path, ["Examplet", "Example Hotel"])
    text = "\n".join(findings)
    assert "docs/inflected.txt:1:" in text
    assert "docs/nbsp.txt:1:" in text
    assert "docs/case.txt:1:" in text


def test_readme_says_to_enter_a_stem():
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    folded = readme.casefold()
    assert "stem" in folded
    assert "examplet" in folded


def test_clock_fallback_and_duplicate_helpers_are_gone():
    import stay_watch.clock as clock
    import stay_watch.config as config
    import stay_watch.notify as notify
    import stay_watch.store as store

    assert not hasattr(clock, "in_warsaw")
    assert not hasattr(config, "weekday_name")
    assert not hasattr(store, "prune_history")
    assert not hasattr(notify, "_group")
    assert hasattr(store, "describe_quote")


def test_dashboard_is_static_and_does_not_name_a_booking_engine():
    text = (REPO / "docs" / "index.html").read_text(encoding="utf-8")
    folded = text.casefold()
    assert "<title>Stay Watch</title>" in text
    assert "data/latest.json" in text
    assert "data/history.json" in text
    assert "prefers-color-scheme" in text
    assert "<script src=" not in folded
    assert "profitroom" not in folded
    assert "example hotel" not in folded
    assert "examplehotel" not in folded


def test_failed_scan_still_reaches_the_public_check_and_the_commit():
    text = (REPO / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
    scan_at = text.index("name: Scan or search")
    check_at = text.index("name: Check public files")
    commit_at = text.index("name: Commit dashboard data")
    fail_at = text.index("name: Report a failed scan")
    assert scan_at < check_at < commit_at < fail_at
    scan_step = text[scan_at:check_at]
    assert "id: scan" in scan_step
    assert "continue-on-error: true" in scan_step
    assert "check-public" not in scan_step
    check_step = text[check_at:commit_at]
    assert "id: check" in check_step
    assert "if:" not in check_step
    assert "check-public" in check_step
    commit_step = text[commit_at:fail_at]
    assert "steps.check.outcome == 'success'" in commit_step
    assert "inputs.mode != 'search'" in commit_step
    fail_step = text[fail_at:]
    assert "steps.scan.outcome == 'failure'" in fail_step
    assert "exit 1" in fail_step


def test_push_rebases_and_retries_three_times():
    text = (REPO / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
    assert "fetch-depth: 0" in text
    assert "git pull --rebase origin" in text
    assert "for attempt in 1 2 3" in text


def test_scan_workflow_is_scheduled_and_does_not_print_secrets():
    text = (REPO / ".github" / "workflows" / "scan.yml").read_text(encoding="utf-8")
    assert 'cron: "17 */3 * * *"' in text
    assert "workflow_dispatch" in text
    assert "contents: write" in text
    assert "check-public" in text
    assert "github-actions[bot]" in text
    assert "cancel-in-progress: false" in text
    assert "echo" not in text.split("echo \"No changes in docs/data\"")[0]
    test = (REPO / ".github" / "workflows" / "test.yml").read_text(encoding="utf-8")
    assert "pytest" in test
    assert 'python-version: "3.12"' in test
