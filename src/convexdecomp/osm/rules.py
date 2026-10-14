"""The footprint checks, read from ``config/checks.json``, the one place they are defined.

The file has the layout of BEM4AI's ``config/checks.json`` (`helpers.rules` there), so the
footprint checks the two pipelines share are the same entries:

- the thresholds, by section: ``footprint``, ``footprint_cleanup`` and ``zones``;
- the checks, in the list ``checks``. Each entry has a ``name``; a ``kind`` (``remove``,
  ``review`` or ``rerun``); ``first_stage``, the stage where it runs first; ``columns``, the
  measurements it reads; ``expression``, which ``DataFrame.eval`` evaluates over those columns and
  which is true for a failing footprint, with ``{section.key}`` standing for a threshold; and
  ``meaning``, what a failure says about the footprint. A check whose ``first_stage`` is 3 may also
  have ``at_draw``, which only BEM4AI's draw reads.

The stage numbers are BEM4AI's: 3 judges the footprint as it was selected (after the mapping
cleanup, `convexdecomp.osm.footprints.clean_footprints`), 4 the footprint after the geometric
preprocessing. A check runs at every stage whose measurements hold all of its ``columns``, so the
footprint checks of stage 3 judge the preprocessed footprint again.

What a failing footprint gets depends on the check's ``kind``: ``remove`` drops it at once,
``review`` selects it for removal unless the review log keeps it, ``rerun`` only reports.

`load_rules` reads the file and refuses a check whose expression does not parse, reads other
columns than its ``columns``, or names a threshold the file lacks.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

__all__ = [
    "CHECKS_KEY",
    "CHECK_FIELDS",
    "CHECK_KINDS",
    "DEFAULT_CONFIG",
    "FOOTPRINT_STAGE",
    "OPTIONAL_CHECK_FIELDS",
    "PREPROCESSED_STAGE",
    "Check",
    "Rules",
    "expression_columns",
    "load_rules",
    "parse_checks",
]

#: The repository's checks: ``config/checks.json`` at the project root.
DEFAULT_CONFIG = Path(__file__).resolve().parents[3] / "config" / "checks.json"
#: The key of the check list; every other key is a section of thresholds.
CHECKS_KEY = "checks"
CHECK_KINDS = ("remove", "review", "rerun")
#: The keys of one check in the list,
CHECK_FIELDS = ("name", "kind", "first_stage", "columns", "expression", "meaning")
#: and the key a stage-3 check may add (BEM4AI's draw reads it).
OPTIONAL_CHECK_FIELDS = ("at_draw",)
#: The footprint as selected, after the mapping cleanup (BEM4AI Stage 3).
FOOTPRINT_STAGE = 3
#: The footprint after the geometric preprocessing (BEM4AI Stage 4).
PREPROCESSED_STAGE = 4
_THRESHOLD = re.compile(r"\{([a-z_]+)\.([a-z0-9_]+)\}")


@dataclass(frozen=True)
class Check:
    """One check of ``config/checks.json`` (see the module docstring)."""

    name: str
    kind: str
    first_stage: int
    columns: tuple[str, ...]
    expression: str
    meaning: str
    at_draw: bool = True

    @property
    def thresholds(self) -> tuple[str, ...]:
        """The ``section.key`` names of the thresholds the expression uses."""
        return tuple(f"{section}.{key}" for section, key in _THRESHOLD.findall(self.expression))

    def rule(self, thresholds: Mapping) -> str:
        """The expression with its thresholds filled in."""
        return _THRESHOLD.sub(lambda match: repr(thresholds[match.group(1)][match.group(2)]), self.expression)

    def reads(self, columns: Collection[str]) -> bool:
        """Whether `columns` hold every column the check reads."""
        return all(column in columns for column in self.columns)

    def failing(self, frame: pd.DataFrame, thresholds: Mapping) -> np.ndarray:
        """Where the rows of `frame` fail the check; a result that is missing counts as passing."""
        mask = pd.Series(frame.eval(self.rule(thresholds), engine="python"), index=frame.index)
        return mask.fillna(False).astype(bool).to_numpy()


def expression_columns(expression: str) -> tuple[str, ...]:
    """The names an expression reads, in the order they first appear (method and attribute names left out)."""
    tree = ast.parse(expression.strip(), mode="eval")
    names = sorted((node.col_offset, node.id) for node in ast.walk(tree) if isinstance(node, ast.Name))
    return tuple(dict.fromkeys(name for _, name in names))


def parse_checks(entries: object, thresholds: Mapping, source: str = "config/checks.json") -> tuple[Check, ...]:
    """The check list, each entry checked against `thresholds`.

    Raises ValueError for an entry without exactly the keys of `CHECK_FIELDS` (and those of
    `OPTIONAL_CHECK_FIELDS` it may add), a name used twice, an unknown kind, a threshold the file
    does not have, an expression that does not parse, or one that reads other columns than its
    ``columns``.
    """
    if not isinstance(entries, list):
        raise ValueError(f"{source}: {CHECKS_KEY!r} must be a list of checks")
    checks: list[Check] = []
    for position, entry in enumerate(entries):
        if (not isinstance(entry, Mapping) or not set(CHECK_FIELDS) <= set(entry)
                or set(entry) - set(CHECK_FIELDS) - set(OPTIONAL_CHECK_FIELDS)):
            raise ValueError(f"{source}, {CHECKS_KEY}[{position}]: a check has the keys "
                             f"{', '.join(CHECK_FIELDS)} and may have {', '.join(OPTIONAL_CHECK_FIELDS)}")
        check = Check(str(entry["name"]), str(entry["kind"]), int(entry["first_stage"]),
                      tuple(str(column) for column in entry["columns"]), str(entry["expression"]),
                      str(entry["meaning"]), entry.get("at_draw", True) is True)
        where = f"{source}, check {check.name!r}"
        if any(check.name == other.name for other in checks):
            raise ValueError(f"{where}: the name is used twice")
        if check.kind not in CHECK_KINDS:
            raise ValueError(f"{where}: kind must be one of {CHECK_KINDS}, not {check.kind!r}")
        for name in check.thresholds:
            section, key = name.split(".")
            if not isinstance(thresholds.get(section), Mapping) or key not in thresholds[section]:
                raise ValueError(f"{where}: the threshold {{{name}}} is not in the file")
        try:
            read = expression_columns(check.rule(thresholds))
        except SyntaxError as error:
            raise ValueError(f"{where}: the expression does not parse: {check.expression!r}") from error
        if set(read) != set(check.columns):
            raise ValueError(f"{where}: the expression reads {sorted(read)}, but its columns are "
                             f"{sorted(check.columns)}")
        checks.append(check)
    return tuple(checks)


@dataclass(frozen=True)
class Rules:
    """Checks and the thresholds they read: what `load_rules` reads from ``config/checks.json``."""

    checks: tuple[Check, ...]
    thresholds: Mapping

    def available(self, columns: Collection[str], stage: int | None = None) -> tuple[Check, ...]:
        """The checks whose columns `columns` hold; with `stage`, only those that run there (``first_stage <= stage``)."""
        return tuple(check for check in self.checks
                     if check.reads(columns) and (stage is None or check.first_stage <= stage))

    def evaluate(self, frame: pd.DataFrame, stage: int | None = None) -> pd.DataFrame:
        """Which checks each row of `frame` fails, as BEM4AI's checks tables record it.

        Returns, on the frame's index, ``hard_failures``, ``review_flags`` and ``rerun_flags``: the
        names of the failed checks of kind ``remove``, ``review`` and ``rerun``, joined by ``;``
        ("" where none failed). Only the checks `available` on the frame's columns are evaluated.
        """
        found = {kind: [[] for _ in range(len(frame))] for kind in CHECK_KINDS}
        for check in self.available(frame.columns, stage):
            for position in np.flatnonzero(check.failing(frame, self.thresholds)):
                found[check.kind][position].append(check.name)
        return pd.DataFrame({
            "hard_failures": [";".join(names) for names in found["remove"]],
            "review_flags": [";".join(names) for names in found["review"]],
            "rerun_flags": [";".join(names) for names in found["rerun"]],
        }, index=frame.index)

    def describe(self, stage: int | None = None) -> pd.DataFrame:
        """One row per check (that runs at `stage`): its name, kind, first stage and rule with the thresholds filled in."""
        return pd.DataFrame([
            {"check": check.name, "kind": check.kind, "first_stage": check.first_stage,
             "rule": check.rule(self.thresholds), "meaning": check.meaning}
            for check in self.checks if stage is None or check.first_stage <= stage
        ])


def load_rules(path: str | Path = DEFAULT_CONFIG) -> Rules:
    """The checks and thresholds of a checks file (by default the repository's ``config/checks.json``)."""
    path = Path(path)
    config = json.loads(path.read_text(encoding="utf-8"))
    if CHECKS_KEY not in config:
        raise ValueError(f"{path} has no {CHECKS_KEY!r} list")
    thresholds = {key: value for key, value in config.items() if key != CHECKS_KEY}
    return Rules(parse_checks(config[CHECKS_KEY], thresholds, source=str(path)), thresholds)
