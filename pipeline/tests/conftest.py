import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
SCHEMAS_DIR = REPO_ROOT / "pipeline" / "schemas"
DATA_HISTORY_DIR = REPO_ROOT / "data" / "history"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def data_history_dir() -> Path:
    return DATA_HISTORY_DIR


@pytest.fixture
def load_fixture():
    def _load(name: str) -> dict:
        with open(FIXTURES_DIR / name) as f:
            return json.load(f)

    return _load


@pytest.fixture
def load_schema():
    def _load(file_metric: str) -> dict:
        with open(SCHEMAS_DIR / f"{file_metric}.schema.json") as f:
            return json.load(f)

    return _load


@pytest.fixture
def sanity_rules() -> dict:
    with open(REPO_ROOT / "pipeline" / "sanity_rules.json") as f:
        return json.load(f)


@pytest.fixture
def model_constants() -> dict:
    with open(REPO_ROOT / "pipeline" / "model_constants.json") as f:
        return json.load(f)


@pytest.fixture
def known_gaps() -> dict:
    with open(REPO_ROOT / "pipeline" / "known_gaps.json") as f:
        return json.load(f)
