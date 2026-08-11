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


#: The one job allowed to write contents, and why. An explicit pair rather than a loosened
#: rule: a job gaining this privilege has to be a decision someone made on purpose, which is
#: the whole point of the test below.
MAY_WRITE_CONTENTS = {
    ("packaging.yml", "attach"): (
        "Uploads the built installer and disk image to the release (#216), which cannot be "
        "done with a read token. Acceptable only because that job installs nothing, checks "
        "out nothing and uses no third-party action -- so the token never shares a job with "
        "the dependency tree. tests/packaging_test.py::TestTheAttachJob pins those properties."
    ),
}


def test_every_job_runs_with_least_privilege_permissions():
    """A job with no `permissions:` block inherits the repository default.

    That default is `write` unless someone changes it, which hands a token able to push to
    master and edit releases to jobs that `pip install` a large third-party dependency tree.
    Almost nothing here needs to write contents, so an absent block is an oversight rather
    than a choice -- and an invisible one, because CI stays green either way.

    Checked per job against the effective value (job-level overrides workflow-level) rather
    than against a pinned list, so a newly added workflow or job cannot slip in without a
    decision. Scopes other than `contents` are left alone: CodeQL genuinely needs
    `security-events: write` to upload its results.

    The single exception is listed in MAY_WRITE_CONTENTS above, with its reason. Adding to that
    list is the decision; inheriting the privilege by accident is what this prevents.
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
            if (path.name, job_name) in MAY_WRITE_CONTENTS:
                continue
            assert effective.get("contents") in ("read", "none"), (
                f"{path.name}: job {job_name!r} runs with contents: "
                f"{effective.get('contents')!r}. If that is deliberate, add it to "
                "MAY_WRITE_CONTENTS with the reason; otherwise it is an oversight."
            )


def test_no_allowance_outlives_the_job_it_was_written_for():
    """An allowance for a job that no longer exists is an unnoticed hole.

    If `attach` is renamed or removed, MAY_WRITE_CONTENTS has to be updated with it, rather
    than silently permitting a name that a different job might later take.
    """
    for (filename, job_name), reason in MAY_WRITE_CONTENTS.items():
        workflow = yaml.safe_load((WORKFLOW.parent / filename).read_text(encoding="utf-8"))
        assert job_name in workflow["jobs"], f"{filename} has no job {job_name!r}, but it is still allowed to write"
        assert reason.strip(), f"{filename}:{job_name} is allowed to write with no reason given"


def test_no_matrix_cancels_its_siblings_on_first_failure():
    """Every matrix job must set `fail-fast: false`.

    With the default, a failure on one leg cancels the rest mid-run, and a cancelled job
    reports as a *failure* carrying no test output. One real failure becomes several, most of
    which say nothing about why -- diagnosing one meant reading the raw log to find "The
    operation was canceled". Whether a failure is one platform or all of them is usually the
    first question, and this is the setting that destroys that information.

    Checked across every workflow rather than a pinned list: default.yml was the outlier while
    the other two already set it, which is exactly the drift this catches.
    """
    workflows = sorted(WORKFLOW.parent.glob("*.yml"))
    assert workflows, "no workflows found; the glob or the path is wrong"
    for path in workflows:
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job_name, job in workflow["jobs"].items():
            strategy = job.get("strategy")
            if strategy is None or "matrix" not in strategy:
                continue  # not a matrix job; nothing to cancel
            assert strategy.get("fail-fast") is False, (
                f"{path.name}: job {job_name!r} leaves fail-fast at its default, so one "
                "failing leg will cancel the others and report them as failures with no "
                "test output."
            )
