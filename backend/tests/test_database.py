import json
from pathlib import Path

from personal_os.database import canonical_resume_path


def test_canonical_resume_is_the_root_git_artifact() -> None:
    path = canonical_resume_path()

    assert path.parts[-3:] == ("artifacts", "career", "main.json")
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)
    assert path == Path(__file__).resolve().parents[2] / "artifacts/career/main.json"
