import re

from erp_engine import ENGINE_VERSION


def test_engine_version_is_semver() -> None:
    assert re.fullmatch(r"\d+\.\d+\.\d+", ENGINE_VERSION)
