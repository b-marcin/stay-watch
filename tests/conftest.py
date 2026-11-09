import pytest

# Names stay_watch reads from the process environment. Tests that need one
# set it themselves (monkeypatch or a subprocess env). Clearing them here
# stops the runner (GITHUB_ACTIONS, hotel secrets, DRY_RUN) from leaking in.
_CLEARED_ENV = (
    "GITHUB_ACTIONS",
    "HOTEL_SLUG",
    "HOTEL_NAME_TERMS",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "ALERT_TO",
    "SMTP_HOST",
    "SMTP_PORT",
    "DRY_RUN",
)


@pytest.fixture(autouse=True)
def _clear_stay_watch_env(monkeypatch):
    for name in _CLEARED_ENV:
        monkeypatch.delenv(name, raising=False)
