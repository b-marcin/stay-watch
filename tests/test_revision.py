import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stay_watch.config import load_secrets, load_watch
from stay_watch.profitroom import FixtureClient
from stay_watch.service import execute_scan
from stay_watch.store import Store


@pytest.mark.parametrize('newline', ['\n', '\r\n'])
def test_config_revision_matches_git_blob_identity(tmp_path, newline):
    raw = ('{\n  "version": 1,\n  "description": "Zażółć"\n}\n').replace('\n', newline).encode('utf-8')
    path = tmp_path / 'config.json'
    path.write_bytes(raw)
    git_revision = subprocess.run(
        ['git', 'hash-object', '--stdin'], input=raw, capture_output=True, check=True,
    ).stdout.decode('ascii').strip()

    assert load_watch(path).revision == git_revision


def test_scan_publishes_the_configuration_it_evaluated(tmp_path):
    path = tmp_path / 'config.json'
    path.write_text(
        '{"version":1,"searches":[{"id":"trip","from":"2026-12-04","to":"2026-12-06"}]}',
        encoding='utf-8',
    )
    config = load_watch(path)
    secrets = load_secrets({'HOTEL_SLUG': 'examplehotel', 'DRY_RUN': '1'})
    store = Store(tmp_path / 'docs', secrets.terms)
    client = FixtureClient(Path(__file__).parent / 'fixtures')

    execute_scan(config, secrets, store, client, datetime(2026, 11, 11, tzinfo=timezone.utc))

    assert store.load_latest()['config_revision'] == config.revision
