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
