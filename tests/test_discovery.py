"""Project ignore rules applied to built-in file discovery tools."""
from __future__ import annotations

from pathlib import Path

import pytest

import noval.builtins as builtins
from noval.builtins import glob, grep, list_directory, read_file
from noval.tools import Context, ToolError


def _ctx(workdir):
    return Context(workdir=workdir)


def _write(path, text="content"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_discovery_combines_gitignore_and_llmignore(tmp_path):
    _write(tmp_path / ".gitignore", "dist/\n")
    _write(tmp_path / ".llmignore", "node_modules/\n*.map\n")
    _write(tmp_path / "src" / "app.py", "TODO visible")
    _write(tmp_path / "dist" / "bundle.py", "TODO hidden dist")
    _write(tmp_path / "node_modules" / "dep.py", "TODO hidden dependency")
    _write(tmp_path / "src" / "app.js.map", "TODO hidden map")
    context = _ctx(tmp_path)

    root_listing = list_directory(context)
    matches = glob(context, "**/*.py")
    search = grep(context, "TODO")

    assert "src/" in root_listing
    assert "dist/" not in root_listing
    assert "node_modules/" not in root_listing
    assert "src\\app.py" in matches or "src/app.py" in matches
    assert "bundle.py" not in matches
    assert "dep.py" not in matches
    assert "app.py" in search
    assert "bundle.py" not in search
    assert "dep.py" not in search
    assert "app.js.map" not in search


def test_recursive_discovery_prunes_ignored_directories_before_descent(
    tmp_path, monkeypatch,
):
    _write(tmp_path / ".llmignore", "node_modules/\n")
    _write(tmp_path / "src" / "app.py", "visible")
    _write(tmp_path / "node_modules" / "package" / "dep.py", "hidden")
    visited = []
    real_walk = builtins.os.walk

    def observed_walk(root):
        for dirpath, dirnames, filenames in real_walk(root):
            visited.append(Path(dirpath).relative_to(tmp_path).as_posix())
            yield dirpath, dirnames, filenames

    monkeypatch.setattr(builtins.os, "walk", observed_walk)

    matches = glob(_ctx(tmp_path), "**/*.py")

    assert "app.py" in matches
    assert all(not path.startswith("node_modules") for path in visited)


def test_non_recursive_glob_does_not_walk_the_repository(tmp_path, monkeypatch):
    _write(tmp_path / "root.py", "visible")
    _write(tmp_path / "nested" / "module.py", "not part of a shallow glob")

    def unexpected_walk(_root):
        raise AssertionError("non-recursive glob must not walk the repository")

    monkeypatch.setattr(builtins.os, "walk", unexpected_walk)

    matches = glob(_ctx(tmp_path), "*.py")

    assert "root.py" in matches
    assert "module.py" not in matches


def test_llmignore_can_reinclude_gitignored_file(tmp_path):
    _write(tmp_path / ".gitignore", "generated/*.py\n")
    _write(tmp_path / ".llmignore", "!generated/keep.py\n")
    _write(tmp_path / "generated" / "drop.py", "drop")
    _write(tmp_path / "generated" / "keep.py", "keep")

    matches = glob(_ctx(tmp_path), "**/*.py")

    assert "keep.py" in matches
    assert "drop.py" not in matches


def test_discovery_rules_refresh_after_ignore_file_changes(tmp_path):
    _write(tmp_path / "cache" / "artifact.txt", "generated")
    context = _ctx(tmp_path)

    assert "cache/" in list_directory(context)

    _write(tmp_path / ".llmignore", "cache/\n")

    assert "cache/" not in list_directory(context)
    assert "artifact.txt" not in glob(context, "**/*.txt")


def test_ignored_file_remains_available_to_explicit_read(tmp_path):
    _write(tmp_path / ".llmignore", "reports/\n")
    _write(tmp_path / "reports" / "result.txt", "explicitly readable")
    context = _ctx(tmp_path)

    assert "reports/" not in list_directory(context)
    assert "result.txt" not in glob(context, "**/*.txt")
    assert "explicitly readable" in read_file(context, "reports/result.txt")


def test_ignored_sibling_is_not_suggested_by_read_file(tmp_path):
    _write(tmp_path / ".llmignore", "config.yaml\n")
    _write(tmp_path / "config.yaml", "setting: value")

    with pytest.raises(ToolError) as error:
        read_file(_ctx(tmp_path), "config.yml")

    assert "did you mean" not in str(error.value)
    assert "config.yaml" not in str(error.value)


def test_vcs_directories_are_always_hidden_from_discovery(tmp_path):
    _write(tmp_path / ".git" / "config", "internal")
    _write(tmp_path / "visible.txt", "visible")
    context = _ctx(tmp_path)

    assert ".git/" not in list_directory(context)
    assert ".git" not in glob(context, "**/*")
    assert ".git" not in grep(context, "internal")
