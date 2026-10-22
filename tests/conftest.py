import shutil

import pytest

from agent.config import ROOT


@pytest.fixture
def sample_data(tmp_path):
    """Only bundled samples: never read or schedule the user's local materials."""
    library = tmp_path / "library"
    library.mkdir()
    for name in ("01_agent_basics.md", "02_context_and_mcp.md", "03_experiment_checklist.txt"):
        shutil.copyfile(ROOT / "data" / "library" / name, library / name)
    shutil.copyfile(ROOT / "data" / "reading_tasks.csv", tmp_path / "reading_tasks.csv")
    return tmp_path
