# Copyright 2026 dupeGuru contributors
#
# This software is licensed under the "GPLv3" License as described in the "LICENSE" file,
# which should be included with this package. The terms are also available at
# http://www.gnu.org/licenses/gpl-3.0.html

"""The CI matrix must expand to the jobs it looks like it expands to.

Adding the PyQt5 fallback as a bare `include` entry did not add a leg -- it silently
*converted* the existing ubuntu/3.12 job into the fallback leg. PyQt6 lost its 3.12 Linux
coverage and `coverage.xml` stopped being uploaded, and CI stayed green throughout. The only
signal was that adding a leg had not changed the number of checks.

GitHub merges an include entry into an existing combination when every key it shares with
the base matrix matches that combination; it creates a new job only when it would *overwrite*
a base value. That rule is easy to violate by accident and invisible when violated, so it is
asserted here rather than left to be noticed.

These parse the workflow. They deliberately do not skip when PyYAML is missing: a guard that
turns "cannot check" into a silent pass is the same failure this file exists to prevent.
PyYAML is declared in requirements-extra.txt for that reason.
"""

import itertools
from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "default.yml"

# The jobs the test matrix is expected to produce. Pinned rather than derived, so that a leg
# quietly disappearing or merging into another has to be an explicit edit here too.
EXPECTED_TEST_JOBS = {
    ("ubuntu-latest", "3.10", "pyqt6"),
    ("ubuntu-latest", "3.12", "pyqt6"),
    ("ubuntu-latest", "3.14", "pyqt6"),
    ("windows-latest", "3.12", "pyqt6"),
    ("macos-latest", "3.12", "pyqt6"),
    ("ubuntu-latest", "3.12", "pyqt5"),
}


def _matrix():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["test"]["strategy"]["matrix"]


def _base_combinations(matrix):
    keys = [k for k in matrix if k != "include"]
    return [dict(zip(keys, values)) for values in itertools.product(*(matrix[k] for k in keys))], keys


def test_every_include_creates_its_own_job():
    """The rule that was violated in #56.

    An include whose base-matrix keys *all* match an existing combination is folded into that
    job. Removing `qt-binding` from the base matrix, for instance, would make the pyqt5
    include collide with the ubuntu/3.12 combination and silently convert it again.
    """
    matrix = _matrix()
    base, base_keys = _base_combinations(matrix)
    for include in matrix.get("include", []):
        shared = {k: v for k, v in include.items() if k in base_keys}
        collisions = [c for c in base if all(c[k] == v for k, v in shared.items())]
        assert not collisions, (
            f"include {include} shares every base-matrix key with {collisions}, so GitHub "
            "will merge it into that job instead of adding one. Give it a value that "
            "overwrites a base-matrix value (that is what qt-binding is for)."
        )


def test_matrix_expands_to_the_expected_jobs():
    matrix = _matrix()
    base, base_keys = _base_combinations(matrix)
    # .get so a missing dimension reports as a readable diff rather than a bare KeyError.
    keys = ("os", "python-version", "qt-binding")
    jobs = {tuple(c.get(k, "<missing>") for k in keys) for c in base}
    for include in matrix.get("include", []):
        jobs.add(tuple(include.get(k, "<missing>") for k in keys))
    assert jobs == EXPECTED_TEST_JOBS, (
        "the CI test matrix no longer expands to the expected job set; update "
        "EXPECTED_TEST_JOBS deliberately if this change is intended.\n"
        f"  missing: {sorted(EXPECTED_TEST_JOBS - jobs)}\n"
        f"  unexpected: {sorted(jobs - EXPECTED_TEST_JOBS)}"
    )


def test_both_qt_bindings_are_exercised():
    """The PyQt5 fallback must keep having a leg; that is the whole point of supporting it."""
    matrix = _matrix()
    base, _ = _base_combinations(matrix)
    bindings = {c.get("qt-binding", "<missing>") for c in base}
    bindings |= {i.get("qt-binding", "<missing>") for i in matrix.get("include", [])}
    assert bindings == {"pyqt6", "pyqt5"}, f"expected both bindings to have CI legs, found {bindings}"


def test_artifact_uploads_exclude_the_fallback_leg():
    """Both uploads are keyed on os/python-version, which the fallback leg also matches.

    Without an exclusion they collide on artifact name and fail the job; with one that is
    written as `!matrix.qt-binding` they suppress the *real* leg instead, which is how
    coverage.xml silently stopped being uploaded.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["test"]["steps"]
    uploads = [s for s in steps if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert uploads, "no artifact upload steps found"
    for step in uploads:
        condition = step.get("if", "")
        assert "matrix.qt-binding != 'pyqt5'" in condition, (
            f"upload step {step.get('name')!r} must exclude the fallback leg by value; "
            f"testing for the key's presence suppresses the wrong job. Found: {condition!r}"
        )


def test_every_job_runs_with_least_privilege_permissions():
    """A job with no `permissions:` block inherits the repository default.

    That default is `write` unless someone changes it, which hands a token able to push to
    master and edit releases to jobs that `pip install` a large third-party dependency tree.
    Nothing in these workflows needs to write contents, so an absent block is an oversight
    rather than a choice -- and an invisible one, because CI stays green either way.

    Checked per job against the effective value (job-level overrides workflow-level) rather
    than against a pinned list, so a newly added workflow or job cannot slip in without a
    decision. Scopes other than `contents` are left alone: CodeQL genuinely needs
    `security-events: write` to upload its results.
    """
    workflows = sorted(WORKFLOW.parent.glob("*.yml"))
    assert workflows, "no workflows found; the glob or the path is wrong"
    for path in workflows:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        default = workflow.get("permissions")
        for job_name, job in workflow["jobs"].items():
            effective = job.get("permissions", default)
            assert effective is not None, (
                f"{path.name}: job {job_name!r} declares no permissions and the workflow "
                "sets no default, so it inherits the repository default. Add "
                "`permissions:\n  contents: read` at the top of the workflow."
            )
            assert effective.get("contents") in ("read", "none"), (
                f"{path.name}: job {job_name!r} runs with contents: "
                f"{effective.get('contents')!r}; none of these jobs write to the repository."
            )
