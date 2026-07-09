import pytest

from noval.confinement import (
    ConfinementPolicy,
    PathAccess,
    PathConfinementError,
    assert_path_allowed,
)


def test_workspace_policy_allows_paths_inside_workdir(tmp_path):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    policy = ConfinementPolicy.workspace(workdir)

    assert_path_allowed(policy, workdir, workdir / "src" / "a.py", PathAccess.READ)
    assert_path_allowed(policy, workdir, workdir / "src" / "a.py", PathAccess.WRITE)


def test_workspace_policy_blocks_parent_escape(tmp_path):
    workdir = tmp_path / "repo"
    workdir.mkdir()
    outside = tmp_path / "secret.txt"
    policy = ConfinementPolicy.workspace(workdir)

    with pytest.raises(PathConfinementError) as exc:
        assert_path_allowed(policy, workdir, outside, PathAccess.READ)

    assert exc.value.access is PathAccess.READ
    assert exc.value.path == outside.resolve()
    assert exc.value.roots == (workdir.resolve(),)


def test_expanded_read_does_not_expand_write_roots(tmp_path):
    workdir = tmp_path / "repo"
    docs = tmp_path / "docs"
    workdir.mkdir()
    docs.mkdir()
    policy = ConfinementPolicy.expanded_read(workdir, [docs])

    assert_path_allowed(policy, workdir, docs / "note.md", PathAccess.READ)
    with pytest.raises(PathConfinementError):
        assert_path_allowed(policy, workdir, docs / "note.md", PathAccess.WRITE)


def test_disabled_policy_allows_any_path(tmp_path):
    workdir = tmp_path / "repo"
    outside = tmp_path / "outside.txt"
    workdir.mkdir()

    assert_path_allowed(ConfinementPolicy.disabled(), workdir, outside, PathAccess.READ)
    assert_path_allowed(ConfinementPolicy.disabled(), workdir, outside, PathAccess.WRITE)
