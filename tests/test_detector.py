from detector import FailureDetector


def test_diagnoses_common_failures():
    detector = FailureDetector()

    assert detector.diagnose("scrape_url", RuntimeError("429 Too Many Requests")) == "rate_limit"
    assert detector.diagnose("send_notification", RuntimeError("401 Unauthorized")) == "auth_failure"
    assert detector.diagnose("scrape_url", RuntimeError("Connection timeout after 30s")) == "timeout"
    assert detector.diagnose("scrape_url", RuntimeError("503 Service Unavailable")) == "cascade_failure"
    assert detector.diagnose("scrape_url", RuntimeError("No LLM provider configured. Set GROQ_API_KEY.")) == "auth_failure"


def test_infinite_loop_after_repeated_errors():
    detector = FailureDetector()
    for _ in range(3):
        detector.record_call("scrape_url", "error")

    assert detector.is_infinite_loop("scrape_url") is True
