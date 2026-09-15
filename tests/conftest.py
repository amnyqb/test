from __future__ import annotations

from pathlib import Path

import pytest

from tests.fixtures.make_fixtures import build_all


@pytest.fixture(scope="session")
def fixtures(tmp_path_factory) -> dict[str, Path]:
    return build_all(tmp_path_factory.mktemp("synthetic"))


@pytest.fixture
def blob_dir(tmp_path) -> Path:
    return tmp_path / "blobs"
