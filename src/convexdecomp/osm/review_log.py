"""The review log: every manual decision on a footprint, kept across runs, in BEM4AI's format.

The log is one CSV with one row per footprint and review stage, with the columns of BEM4AI's
``data/review/review_log.csv`` (`helpers.review_log` there), so a log of either pipeline can be read
by the other:

| Column | Meaning |
|---|---|
| `source_sample_id` | the footprint, the same in every run (for OSM footprints from BEM4AI, `<city_key>_<OSM id>`) |
| `stage` | `footprints` for the footprints this repository preprocesses; BEM4AI also writes `buildings` and `bem` |
| `decision` | `remove` or `keep` |
| `by` | `manual`, or BEM4AI's automatic checks: `check_hard`, `check_review` |
| `fingerprint` | a hash of the footprint that was judged (`footprint_fingerprint` of the outline as it arrived) |
| `sample_id` | the footprint's `sample_id` when it was decided, for people |
| `marked_at` | when, in UTC |
| `note` | an optional reason |

A decision applies to a footprint while the footprint still has the fingerprint it was judged with
(`mark_states`: `applies`, `changed`, or `absent` from the table). A manual `keep` overrides the
review checks of ``config/checks.json``.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

__all__ = [
    "DECISIONS",
    "IDENTITY_COLUMNS",
    "LOG_COLUMNS",
    "MARKED_BY",
    "STAGES",
    "add_marks",
    "footprint_identity",
    "keep_marks",
    "mark_states",
    "read_log",
    "remove_marks",
]

STAGES = ("footprints", "buildings", "bem")
LOG_COLUMNS = ("source_sample_id", "stage", "decision", "by", "fingerprint", "sample_id", "marked_at", "note")
DECISIONS = ("remove", "keep")
MARKED_BY = ("manual", "check_hard", "check_review")
IDENTITY_COLUMNS = ("source_sample_id", "sample_id", "fingerprint")


def footprint_identity(footprints: pd.DataFrame) -> pd.DataFrame:
    """The identity table of a footprint table: `source_sample_id`, `sample_id` and its `footprint_fingerprint`."""
    identity = pd.DataFrame({
        "source_sample_id": footprints["source_sample_id"].astype(str).to_numpy(),
        "sample_id": footprints["sample_id"].astype(str).to_numpy(),
        "fingerprint": footprints["footprint_fingerprint"].astype(str).to_numpy(),
    })
    return identity.drop_duplicates("source_sample_id", keep="first").reset_index(drop=True)


def _empty_log() -> pd.DataFrame:
    return pd.DataFrame({column: pd.Series(dtype=str) for column in LOG_COLUMNS})


def _check_stage(stage: str) -> None:
    if stage not in STAGES:
        raise ValueError(f"Unknown review stage {stage!r}; expected one of {STAGES}.")


def read_log(path: str | Path) -> pd.DataFrame:
    """Every decision in the log, the last one per stage and footprint; a missing file is an empty log."""
    path = Path(path)
    if not path.is_file():
        return _empty_log()
    try:
        log = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        return _empty_log()
    for column, default in (("decision", "remove"), ("by", "manual")):
        if column not in log.columns:
            log[column] = default
        log[column] = log[column].where(log[column].ne(""), default)
    missing = [column for column in LOG_COLUMNS if column not in log.columns]
    if missing:
        raise KeyError(f"{path} is missing the review-log columns {missing}.")
    for column, allowed in (("stage", STAGES), ("decision", DECISIONS), ("by", MARKED_BY)):
        unknown = sorted(set(log[column]) - set(allowed))
        if unknown:
            raise ValueError(f"{path} has unknown {column} values {unknown}.")
    log = log.loc[log["source_sample_id"].str.strip().ne(""), list(LOG_COLUMNS)]
    return log.drop_duplicates(["stage", "source_sample_id"], keep="last").reset_index(drop=True)


def _write_log(log: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = log.sort_values(["stage", "source_sample_id"], kind="stable")[list(LOG_COLUMNS)]
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        ordered.to_csv(temporary, index=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _decide(stage: str, identity: pd.DataFrame, source_ids: Iterable[str], *, decision: str, note: str,
            path: str | Path) -> list[str]:
    """Record a manual `decision` for footprints of `identity`, with their current fingerprint."""
    _check_stage(stage)
    current = identity.set_index("source_sample_id")
    wanted = [source_id for source_id in dict.fromkeys(str(value).strip() for value in source_ids)
              if source_id in current.index]
    if not wanted:
        return []
    path = Path(path)
    log = read_log(path)
    here = log.loc[log["stage"].eq(stage)].set_index("source_sample_id")
    changed = [
        source_id for source_id in wanted
        if source_id not in here.index
        or (here.at[source_id, "fingerprint"], here.at[source_id, "decision"], here.at[source_id, "by"])
        != (current.at[source_id, "fingerprint"], decision, "manual")
    ]
    if changed:
        rows = pd.DataFrame({
            "source_sample_id": changed,
            "stage": stage,
            "decision": decision,
            "by": "manual",
            "fingerprint": [current.at[source_id, "fingerprint"] for source_id in changed],
            "sample_id": [current.at[source_id, "sample_id"] for source_id in changed],
            "marked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "note": [note or (here.at[source_id, "note"] if source_id in here.index else "") for source_id in changed],
        })
        kept = log.loc[~(log["stage"].eq(stage) & log["source_sample_id"].isin(changed))]
        _write_log(pd.concat([kept, rows], ignore_index=True), path)
    return changed


def add_marks(stage: str, identity: pd.DataFrame, source_ids: Iterable[str], *, path: str | Path,
              note: str = "") -> list[str]:
    """Mark footprints of `identity` for removal by hand, with their current fingerprint; returns the ids recorded."""
    return _decide(stage, identity, source_ids, decision="remove", note=note, path=path)


def keep_marks(stage: str, identity: pd.DataFrame, source_ids: Iterable[str], *, path: str | Path,
               note: str = "") -> list[str]:
    """Keep footprints of `identity` by hand, against the review checks; returns the ids recorded."""
    return _decide(stage, identity, source_ids, decision="keep", note=note, path=path)


def remove_marks(stage: str, source_ids: Iterable[str], *, path: str | Path) -> list[str]:
    """Take footprints' decisions at `stage` out of the log; returns the ids that were in it."""
    _check_stage(stage)
    wanted = {str(value).strip() for value in source_ids}
    path = Path(path)
    if not wanted or not path.is_file():
        return []
    log = read_log(path)
    selected = log["stage"].eq(stage) & log["source_sample_id"].isin(wanted)
    removed = sorted(log.loc[selected, "source_sample_id"])
    if removed:
        _write_log(log.loc[~selected], path)
    return removed


def mark_states(stage: str, identity: pd.DataFrame, path: str | Path) -> pd.DataFrame:
    """Every decision of `stage`, with what it means for the footprints in `identity`.

    One row per decision: the log's columns, `current_sample_id` and `current_fingerprint` (empty
    when the footprint is not in `identity`), and `state`: `applies`, `changed` or `absent`.
    """
    _check_stage(stage)
    log = read_log(path)
    marks = log.loc[log["stage"].eq(stage)].reset_index(drop=True)
    current = identity[list(IDENTITY_COLUMNS)].rename(
        columns={"sample_id": "current_sample_id", "fingerprint": "current_fingerprint"})
    states = marks.merge(current, on="source_sample_id", how="left", validate="one_to_one")
    present = states["current_fingerprint"].notna()
    states["state"] = "absent"
    states.loc[present, "state"] = "changed"
    states.loc[present & states["current_fingerprint"].eq(states["fingerprint"]), "state"] = "applies"
    return states.fillna({"current_sample_id": "", "current_fingerprint": ""})
