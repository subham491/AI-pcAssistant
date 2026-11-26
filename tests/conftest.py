from pathlib import Path

import pytest

from assistant.config import Config


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    home = tmp_path / "home"
    home.mkdir()
    return Config(home=home)
