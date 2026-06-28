"""内置工具行为测试 + 文件工具状态机（read-tracker / staleness）。"""
import os
import shutil

import pytest

from noval.builtins import (
    _bash_risk, _wsl_to_windows, edit_file, glob, grep, list_directory, read_file,
    run_bash, write_file,
)
from noval.tools import Context, Risk, ToolError

needs_bash = pytest.mark.skipif(shutil.which("bash") is None, reason="bash 不在 PATH")


def ctx(tmp_path):
    return Context(workdir=tmp_path)


# --- read_file ------------------------------------------------------------
def test_read_file_line_numbers(tmp_path):
    (tmp_path / "a.txt").write_text("hello\nworld\n", encoding="utf-8")
    out = read_file(ctx(tmp_path), "a.txt")
    assert "1\thello" in out and "2\tworld" in out


def test_read_file_not_found_suggests(tmp_path):
    (tmp_path / "config.yaml").write_text("x", encoding="utf-8")
    with pytest.raises(ToolError) as e:
        read_file(ctx(tmp_path), "config.yml")
    assert "config.yaml" in str(e.value)            # did-you-mean


def test_read_file_empty_warns(tmp_path):
    (tmp_path / "e.txt").write_text("", encoding="utf-8")
    assert "空" in read_file(ctx(tmp_path), "e.txt")


def test_read_file_dir_rejected(tmp_path):
    with pytest.raises(ToolError):
        read_file(ctx(tmp_path), ".")


def test_read_file_offset_limit(tmp_path):
    (tmp_path / "n.txt").write_text("\n".join(str(i) for i in range(1, 11)), encoding="utf-8")
    out = read_file(ctx(tmp_path), "n.txt", offset=3, limit=2)
    assert "3\t3" in out and "4\t4" in out and "5\t5" not in out


def test_read_file_large_file_streams_partial(tmp_path):
    # 整文件超过上限 → 整读被拒；但带 offset/limit 可流式读片段（不爆内存）
    f = tmp_path / "big.txt"
    f.write_text("\n".join(f"line{i}" for i in range(50000)), encoding="utf-8")  # ~400KB
    c = ctx(tmp_path)
    with pytest.raises(ToolError) as e:
        read_file(c, "big.txt")
    assert "太大" in str(e.value)
    out = read_file(c, "big.txt", offset=100, limit=3)   # 1-based 第100行 = "line99"
    assert "line99" in out and "line100" in out and "line102" not in out


# --- write_file: read-before-write ---------------------------------------
def test_write_new_file_no_read_needed(tmp_path):
    msg = write_file(ctx(tmp_path), "new.txt", "hi")
    assert "created" in msg and (tmp_path / "new.txt").read_text(encoding="utf-8") == "hi"


def test_write_existing_requires_prior_read(tmp_path):
    (tmp_path / "x.txt").write_text("old", encoding="utf-8")
    with pytest.raises(ToolError) as e:
        write_file(ctx(tmp_path), "x.txt", "new")
    assert "read" in str(e.value) or "读" in str(e.value)


def test_write_after_read_ok(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("old", encoding="utf-8")
    c = ctx(tmp_path)
    read_file(c, "x.txt")
    assert "updated" in write_file(c, "x.txt", "new")
    assert f.read_text(encoding="utf-8") == "new"


# --- edit_file ------------------------------------------------------------
def test_edit_requires_read(tmp_path):
    (tmp_path / "e.py").write_text("a=1", encoding="utf-8")
    with pytest.raises(ToolError):
        edit_file(ctx(tmp_path), "e.py", "a=1", "a=2")


def test_edit_unique_then_replace_all(tmp_path):
    f = tmp_path / "e.py"
    f.write_text("x=1\nx=1", encoding="utf-8")
    c = ctx(tmp_path)
    read_file(c, "e.py")
    with pytest.raises(ToolError) as e:
        edit_file(c, "e.py", "x=1", "x=2")          # 两处匹配 → 拒绝
    assert "2" in str(e.value)
    edit_file(c, "e.py", "x=1", "x=2", replace_all=True)
    assert f.read_text(encoding="utf-8") == "x=2\nx=2"


def test_edit_string_not_found(tmp_path):
    (tmp_path / "e.py").write_text("a=1", encoding="utf-8")
    c = ctx(tmp_path)
    read_file(c, "e.py")
    with pytest.raises(ToolError):
        edit_file(c, "e.py", "zzz", "yyy")


def test_edit_old_equals_new(tmp_path):
    (tmp_path / "e.py").write_text("a=1", encoding="utf-8")
    c = ctx(tmp_path)
    read_file(c, "e.py")
    with pytest.raises(ToolError):
        edit_file(c, "e.py", "a=1", "a=1")


def test_edit_then_edit_again_no_false_stale(tmp_path):
    # 编辑后回写 read_state，紧接着再编辑不该被自己误判为 stale
    f = tmp_path / "e.py"
    f.write_text("a=1\nb=2", encoding="utf-8")
    c = ctx(tmp_path)
    read_file(c, "e.py")
    edit_file(c, "e.py", "a=1", "a=9")
    edit_file(c, "e.py", "b=2", "b=8")
    assert f.read_text(encoding="utf-8") == "a=9\nb=8"


# --- staleness ------------------------------------------------------------
def test_staleness_blocks_write(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("v1", encoding="utf-8")
    c = ctx(tmp_path)
    read_file(c, "s.txt")
    f.write_text("v2-external", encoding="utf-8")          # 外部改动
    rec = next(iter(c.read_state.values()))
    rec.mtime -= 10                                          # 模拟磁盘 mtime 比记录新
    with pytest.raises(ToolError) as e:
        write_file(c, "s.txt", "v3")
    assert "改动过" in str(e.value)


def test_staleness_false_positive_allows_when_content_same(tmp_path):
    # mtime 被推后但内容没变时，内容回退比对应放行
    f = tmp_path / "s.txt"
    f.write_text("same", encoding="utf-8")
    c = ctx(tmp_path)
    read_file(c, "s.txt")
    rec = next(iter(c.read_state.values()))
    rec.mtime -= 10
    assert "updated" in write_file(c, "s.txt", "new-ok")    # 内容未变 → 不误报


# --- list_directory -------------------------------------------------------
def test_list_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    out = list_directory(ctx(tmp_path))
    assert "sub/" in out and "a.txt" in out


# --- glob -----------------------------------------------------------------
def test_glob_mtime_sorted(tmp_path):
    (tmp_path / "old.py").write_text("o", encoding="utf-8")
    (tmp_path / "new.py").write_text("n", encoding="utf-8")
    os.utime(tmp_path / "old.py", (1, 1))                   # old.py 设为很旧
    out = glob(ctx(tmp_path), "*.py")
    assert out.splitlines()[0] == "new.py"                  # 最近的在前


def test_glob_no_match(tmp_path):
    assert "未找到" in glob(ctx(tmp_path), "*.nope")


# --- grep -----------------------------------------------------------------
def test_grep_files_mode(tmp_path):
    (tmp_path / "a.py").write_text("import os\nx=1", encoding="utf-8")
    (tmp_path / "b.py").write_text("y=2", encoding="utf-8")
    out = grep(ctx(tmp_path), "import")
    assert "a.py" in out and "b.py" not in out


def test_grep_content_mode(tmp_path):
    (tmp_path / "a.py").write_text("import os\nx=1", encoding="utf-8")
    out = grep(ctx(tmp_path), "import", output_mode="content")
    assert "a.py:1:import os" in out


def test_grep_excludes_vcs(tmp_path):
    g = tmp_path / ".git"
    g.mkdir()
    (g / "config").write_text("import secret", encoding="utf-8")
    (tmp_path / "a.py").write_text("import os", encoding="utf-8")
    out = grep(ctx(tmp_path), "import")
    assert ".git" not in out and "a.py" in out


def test_grep_glob_filter(tmp_path):
    (tmp_path / "a.py").write_text("TODO", encoding="utf-8")
    (tmp_path / "a.txt").write_text("TODO", encoding="utf-8")
    out = grep(ctx(tmp_path), "TODO", glob_filter="*.py")
    assert "a.py" in out and "a.txt" not in out


def test_grep_no_match(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    assert "未找到" in grep(ctx(tmp_path), "zzzz")


def test_grep_searches_non_utf8_text(tmp_path):
    # latin-1 文本(非 UTF-8)曾被静默跳过；现在 ASCII 匹配能搜到
    (tmp_path / "log.txt").write_bytes("café TODO fix".encode("latin-1"))  # 0xe9 非法 UTF-8
    assert "log.txt" in grep(ctx(tmp_path), "TODO")


def test_grep_skips_true_binary(tmp_path):
    # 含 NUL 的真二进制仍跳过，不污染搜索结果
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01SECRET\x00")
    assert "未找到" in grep(ctx(tmp_path), "SECRET")


# --- run_bash -------------------------------------------------------------
def test_run_bash_echo(tmp_path):
    assert "hello" in run_bash(ctx(tmp_path), "echo hello")


@needs_bash
def test_run_bash_cwd_is_workdir(tmp_path):
    (tmp_path / "marker.txt").write_text("x", encoding="utf-8")
    assert "marker.txt" in run_bash(ctx(tmp_path), "ls")


@needs_bash
def test_run_bash_timeout(tmp_path):
    with pytest.raises(ToolError) as e:
        run_bash(ctx(tmp_path), "sleep 5", timeout=1)
    assert "超时" in str(e.value)


# --- run_bash 动态风险分级 ------------------------------------------------
def test_bash_risk_readonly():
    assert _bash_risk({"command": "grep -n x f"}) is Risk.READ
    assert _bash_risk({"command": "grep x f | grep y | head -20"}) is Risk.READ
    assert _bash_risk({"command": "wc -l f && ls -lh f"}) is Risk.READ
    assert _bash_risk({"command": "sed -n '100,200p' f"}) is Risk.READ
    assert _bash_risk({"command": "zcat a.gz | grep x"}) is Risk.READ      # 扩充的只读命令


def test_bash_risk_safe_redirect_not_dangerous():
    # 2>&1（fd 复制）与 2>/dev/null（丢黑洞）都不是真写文件 —— 不该误判（曾经的 bug）
    assert _bash_risk({"command": 'ls -lh "c:/x" 2>&1'}) is Risk.READ
    assert _bash_risk({"command": "pwd && ls 2>&1"}) is Risk.READ
    assert _bash_risk({"command": "find / -name x 2>/dev/null | head"}) is Risk.READ
    assert _bash_risk({"command": "grep x f > out.txt"}) is Risk.DANGEROUS    # 写真实文件


def test_bash_risk_dangerous():
    assert _bash_risk({"command": "rm -rf /tmp/x"}) is Risk.DANGEROUS      # 非只读程序
    assert _bash_risk({"command": "echo x > f"}) is Risk.DANGEROUS         # 重定向写
    assert _bash_risk({"command": "sed -i 's/a/b/' f"}) is Risk.DANGEROUS  # 原地改
    assert _bash_risk({"command": "cat $(rm x)"}) is Risk.DANGEROUS        # 命令替换
    assert _bash_risk({"command": ""}) is Risk.DANGEROUS                   # 空命令保守


def test_wsl_to_windows_translation():
    assert _wsl_to_windows("/mnt/e/WorkSpace/x") == "E:/WorkSpace/x"
    assert _wsl_to_windows("/mnt/c") == "C:/"                 # 盘根
    assert _wsl_to_windows("relative/path") == "relative/path"  # 非 /mnt 不动
    assert _wsl_to_windows("/usr/local/bin") == "/usr/local/bin"  # 非挂载点不动


def test_bash_risk_cd_whitelisted():
    # cd 无害 → cd X && 只读命令 不该弹窗；但链里真危险的仍拦下
    assert _bash_risk({"command": "cd /mnt/e/proj && grep foo bar.txt"}) is Risk.READ
    assert _bash_risk({"command": "cd /x && rm -rf y"}) is Risk.DANGEROUS


def test_bash_risk_git_readonly():
    assert _bash_risk({"command": "git log --oneline -5"}) is Risk.READ
    assert _bash_risk({"command": "cd /mnt/e/p && git show --stat HEAD"}) is Risk.READ
    assert _bash_risk({"command": "git -C /path status"}) is Risk.READ       # 跳过 -C 参数
    assert _bash_risk({"command": "git --no-pager diff | head"}) is Risk.READ


def test_bash_risk_git_mutating_still_dangerous():
    for cmd in ("git commit -m x", "git push", "git reset --hard HEAD~1",
                "git checkout main", "cd /x && git clean -fd", "git pull"):
        assert _bash_risk({"command": cmd}) is Risk.DANGEROUS, cmd
