import wls


def test_package_version() -> None:
    assert wls.__version__ == "0.6.0.dev1"
