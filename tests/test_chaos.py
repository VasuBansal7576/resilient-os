import pytest

from chaos import clear_chaos, configure_chaos, get_chaos_targets, inject_chaos, set_chaos


@pytest.fixture(autouse=True)
def clean_chaos_state():
    clear_chaos()
    yield
    clear_chaos()


def test_one_shot_chaos_fails_once_then_allows_future_calls():
    set_chaos("scrape_url", "rate_limit", failures=1)

    with pytest.raises(RuntimeError, match="429 Too Many Requests"):
        inject_chaos("scrape_url")

    inject_chaos("scrape_url")

    target = get_chaos_targets()["scrape_url"]
    assert target["calls"] == 2
    assert target["failures_remaining"] == 0


def test_repeat_chaos_fails_every_call_after_trigger():
    set_chaos("send_notification", "auth", failures=1, repeat=True)

    for _ in range(3):
        with pytest.raises(RuntimeError, match="401 Unauthorized"):
            inject_chaos("send_notification")

    target = get_chaos_targets()["send_notification"]
    assert target["calls"] == 3
    assert target["failures_remaining"] == 1


def test_configure_chaos_replaces_existing_rules():
    set_chaos("scrape_url", "rate_limit")

    configure_chaos([{"tool": "send_notification", "error_type": "auth", "failures": 1}])

    assert set(get_chaos_targets()) == {"send_notification"}
    with pytest.raises(RuntimeError, match="401 Unauthorized"):
        inject_chaos("send_notification")
    inject_chaos("send_notification")
