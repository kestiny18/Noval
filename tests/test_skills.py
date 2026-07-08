import json
import stat

import pytest

from noval.builtins import list_skills, load_skill, read_skill_resource, run_skill_script
from noval.skills import SkillRegistry, discover_skills, skill_index_context
from noval.tools import Context, ToolError


def _skill(root, rel, *, name, description, body="Body", resource=True, script=True):
    d = root / rel
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n\n{body}\n",
        encoding="utf-8",
    )
    if resource:
        (d / "references").mkdir()
        (d / "references" / "note.md").write_text("reference text", encoding="utf-8")
    if script:
        (d / "scripts").mkdir()
        script_file = d / "scripts" / "hello.py"
        script_file.write_text(
            "import sys\nprint('hello ' + ' '.join(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        script_file.chmod(script_file.stat().st_mode | stat.S_IXUSR)
    return d


def test_discover_skills_from_claude_codex_cursor_and_noval_dirs(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    _skill(home / ".claude" / "skills", "global-review", name="review", description="global review")
    _skill(workdir / ".codex" / "skills", "project-git", name="git-delivery", description="project git")
    _skill(home / ".cursor" / "skills", "user-cursor", name="cursor-user", description="user cursor")
    _skill(workdir / ".cursor" / "skills", "project-cursor", name="cursor-project", description="project cursor")
    _skill(workdir / ".noval" / "skills", "local", name="local-helper", description="local helper")
    _skill(workdir / ".cursor" / "rules", "ignored", name="cursor-skill", description="must not load")

    skills = discover_skills(workdir, home=home)

    assert {item.name for item in skills} == {
        "review",
        "git-delivery",
        "cursor-user",
        "cursor-project",
        "local-helper",
    }
    assert all(".cursor/rules" not in item.location.replace("\\", "/") for item in skills)
    assert {item.source for item in skills} == {
        "user.claude",
        "project.codex",
        "user.cursor",
        "project.cursor",
        "project.noval",
    }


def test_skill_index_is_lightweight_and_does_not_include_body(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    _skill(workdir / ".claude" / "skills", "bug", name="bug-investigation", description="debug things", body="SECRET BODY")
    registry = SkillRegistry.discover(workdir, home=home)

    index = skill_index_context(registry)

    assert index is not None
    assert "bug-investigation" in index
    assert "debug things" in index
    assert "SECRET BODY" not in index
    assert "load_skill" in index


def test_skill_tools_load_content_resource_and_script(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    _skill(workdir / ".claude" / "skills", "bug", name="bug-investigation", description="debug things")
    registry = SkillRegistry.discover(workdir, home=home)
    ctx = Context(workdir=workdir, skills=registry)

    listed = json.loads(list_skills(ctx))
    assert listed["skills"][0]["name"] == "bug-investigation"
    assert "Body" in load_skill(ctx, "bug-investigation")
    assert read_skill_resource(ctx, "bug-investigation", "references/note.md") == "reference text"
    assert "hello world" in run_skill_script(ctx, "bug-investigation", "scripts/hello.py", "world")


def test_skill_resource_and_script_cannot_escape_root(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    _skill(workdir / ".codex" / "skills", "safe", name="safe", description="safe")
    registry = SkillRegistry.discover(workdir, home=home)
    ctx = Context(workdir=workdir, skills=registry)

    with pytest.raises(ToolError, match="逃逸|绝对路径"):
        read_skill_resource(ctx, "safe", "../secret.txt")
    with pytest.raises(ToolError, match="逃逸|绝对路径"):
        run_skill_script(ctx, "safe", "../evil.py")


def test_duplicate_skill_name_requires_id(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    _skill(home / ".claude" / "skills", "one", name="dup", description="one")
    _skill(workdir / ".claude" / "skills", "two", name="dup", description="two")
    registry = SkillRegistry.discover(workdir, home=home)
    ctx = Context(workdir=workdir, skills=registry)

    with pytest.raises(ToolError, match="不唯一"):
        load_skill(ctx, "dup")

    first_id = registry.skills[0].skill_id
    assert "Body" in load_skill(ctx, first_id)


def test_slug_collisions_get_stable_unique_ids(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    _skill(workdir / ".claude" / "skills", "foo bar", name="one", description="one")
    _skill(workdir / ".claude" / "skills", "foo-bar", name="two", description="two")

    skills = discover_skills(workdir, home=home)

    assert [item.skill_id for item in skills] == [
        "project.claude:foo-bar",
        "project.claude:foo-bar-2",
    ]


def test_skill_snapshot_diff_tracks_added_removed_and_changed(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    kept = _skill(workdir / ".codex" / "skills", "kept", name="kept", description="old")
    removed = _skill(workdir / ".codex" / "skills", "removed", name="removed", description="removed")
    before = SkillRegistry.discover(workdir, home=home).snapshot()

    (kept / "SKILL.md").write_text(
        "---\nname: kept\ndescription: new\n---\n\nChanged body\n",
        encoding="utf-8",
    )
    (removed / "SKILL.md").unlink()
    _skill(workdir / ".codex" / "skills", "added", name="added", description="added")
    after = SkillRegistry.discover(workdir, home=home).snapshot()

    diff = before.diff(after)

    assert diff.added == ["project.codex:added"]
    assert diff.removed == ["project.codex:removed"]
    assert diff.changed == ["project.codex:kept"]
    assert diff.has_changes() is True


def test_list_skills_supports_filter_pagination_and_skill_alias(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    _skill(workdir / ".cursor" / "skills", "review", name="doc-review", description="review architecture docs")
    _skill(workdir / ".cursor" / "skills", "mock", name="trade-mock", description="mock trade interface")
    _skill(workdir / ".codex" / "skills", "write", name="doc-write", description="write docs")
    registry = SkillRegistry.discover(workdir, home=home)
    ctx = Context(workdir=workdir, skills=registry)

    filtered = json.loads(list_skills(ctx, query="review"))
    assert filtered["total"] == 1
    assert filtered["skills"][0]["name"] == "doc-review"

    source_page = json.loads(list_skills(ctx, source="project.cursor", limit=1))
    assert source_page["total"] == 2
    assert source_page["returned"] == 1
    assert source_page["has_more"] is True

    alias = json.loads(list_skills(ctx, skill="trade"))
    assert alias["skills"][0]["name"] == "trade-mock"


def test_list_skills_default_output_stays_compact(tmp_path):
    home = tmp_path / "home"
    workdir = tmp_path / "repo"
    long_desc = "review " + ("very long description " * 30)
    for i in range(40):
        _skill(workdir / ".cursor" / "skills", f"skill-{i:02d}", name=f"skill-{i:02d}", description=long_desc)
    registry = SkillRegistry.discover(workdir, home=home)
    ctx = Context(workdir=workdir, skills=registry)

    out = list_skills(ctx)
    data = json.loads(out)

    assert data["total"] == 40
    assert data["returned"] == 20
    assert data["has_more"] is True
    assert "location" not in data["skills"][0]
    assert len(data["skills"][0]["description"]) <= 183
    assert len(out) < 8000
