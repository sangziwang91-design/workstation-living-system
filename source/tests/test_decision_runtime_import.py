from wls.decision_runtime import DecisionAwareRuntime


def test_decision_runtime_symbol() -> None:
    assert DecisionAwareRuntime.__name__ == "DecisionAwareRuntime"
