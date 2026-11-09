from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


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
