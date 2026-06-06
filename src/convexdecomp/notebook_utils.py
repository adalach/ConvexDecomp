from __future__ import annotations

import shutil
import subprocess
import zipfile
from pathlib import Path

__all__ = [
    "ensure_resplan_dataset",
    "ensure_resplan_repo",
    "find_project_root",
]

DEFAULT_RESPLAN_REPO_URL = "https://github.com/m-agour/ResPlan.git"


def find_project_root(start: Path) -> Path:
    for path in (start, *start.parents):
        if (
            (path / "pyproject.toml").exists()
            and (path / "notebooks").exists()
            and (path / "src" / "convexdecomp").exists()
        ):
            return path
    raise FileNotFoundError(f"Could not locate the ConvexDecomp project root starting at {start}.")


def ensure_resplan_repo(repo_dir: Path, repo_url: str = DEFAULT_RESPLAN_REPO_URL) -> Path:
    archive_path = repo_dir / "ResPlan.zip"
    if archive_path.exists():
        print(f"[ResPlan] upstream repo present: {repo_dir}")
        return repo_dir

    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    if repo_dir.exists() and any(repo_dir.iterdir()):
        raise FileExistsError(
            f"Expected an empty clone target at {repo_dir}, but it already contains files."
        )

    print(f"[ResPlan] cloning {repo_url} into {repo_dir}")
    subprocess.run(["git", "clone", "--depth", "1", repo_url, str(repo_dir)], check=True)
    return repo_dir


def ensure_resplan_dataset(
    dataset_path: Path,
    repo_dir: Path | None = None,
    repo_url: str = DEFAULT_RESPLAN_REPO_URL,
) -> None:
    if dataset_path.exists():
        print(f"[ResPlan] dataset present: {dataset_path}")
        return

    repo_dir = dataset_path.parent if repo_dir is None else repo_dir
    ensure_resplan_repo(repo_dir, repo_url=repo_url)

    archive_path = repo_dir / "ResPlan.zip"
    if not archive_path.exists():
        raise FileNotFoundError(
            f"Expected {archive_path} after cloning {repo_url}, but it was not found."
        )

    dataset_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[ResPlan] extracting {archive_path.name} -> {dataset_path}")
    with zipfile.ZipFile(archive_path) as archive:
        member = next((name for name in archive.namelist() if Path(name).name == "ResPlan.pkl"), None)
        if member is None:
            raise FileNotFoundError("ResPlan.zip does not contain ResPlan.pkl.")
        with archive.open(member) as source, open(dataset_path, "wb") as target:
            shutil.copyfileobj(source, target)

    print(f"[ResPlan] dataset ready: {dataset_path}")
