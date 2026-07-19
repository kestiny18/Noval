# LLM Ignore Discovery Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce repository discovery noise and traversal cost by applying root `.gitignore` and `.llmignore` rules to Noval's built-in discovery tools without restricting explicit file reads.

**Architecture:** Add a small per-session discovery policy that compiles Git-style patterns and refreshes when either root ignore file changes. File discovery tools consult the policy while traversing so ignored directories are pruned before scanning; `read_file`, write tools, subprocesses, confinement, and sandbox behavior remain unchanged.

**Tech Stack:** Python 3.10+, `pathspec` GitIgnoreSpec, pytest.

---

### Task 1: Specify discovery semantics with tests

**Files:**
- Modify: `tests/test_builtins.py`
- Create: `tests/test_discovery.py`

**Steps:**
1. Add failing tests showing root `.gitignore` rules apply to directory listing, globbing, grep, and filename suggestions.
2. Add failing tests showing later `.llmignore` rules add exclusions and can re-include a path with Git-style negation.
3. Add a failing test proving an explicitly addressed ignored file remains readable through `read_file`.
4. Run the targeted tests and confirm they fail before implementation.

### Task 2: Add the discovery policy

**Files:**
- Create: `noval/discovery.py`
- Modify: `noval/tools.py`
- Modify: `noval/agent.py`
- Modify: `pyproject.toml`

**Steps:**
1. Add `pathspec>=1.1,<2` as the Git-style pattern implementation.
2. Implement a workdir-scoped `DiscoveryPolicy` that loads root `.gitignore` followed by `.llmignore`, safely degrades when files are missing or unreadable, and refreshes on file metadata changes.
3. Keep paths outside workdir unaffected and normalize candidate paths to POSIX separators.
4. Attach one policy instance to each tool `Context` so mutable state remains session-local.
5. Run policy unit tests.

### Task 3: Integrate discovery tools

**Files:**
- Modify: `noval/builtins.py`
- Modify: `tests/test_builtins.py`

**Steps:**
1. Filter `list_directory` entries using the policy.
2. Replace recursive glob expansion with a pruned traversal that preserves existing glob behavior for common patterns.
3. Prune ignored directories and files during `grep` traversal.
4. Exclude ignored sibling names from missing-file suggestions.
5. Preserve explicit `read_file` behavior and existing path-jail checks.
6. Run built-in tool tests.

### Task 4: Document the contract

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `DESIGN.md`
- Modify: `CHANGELOG.md`
- Modify: `.gitignore`

**Steps:**
1. Document precedence: built-in VCS pruning, root `.gitignore`, then root `.llmignore`.
2. State that ignored paths remain available to explicit reads and external processes; this is a discovery optimization, not a security boundary.
3. Provide a concise `.llmignore` example for generated output and dependency directories.
4. Replace the existing mojibake comments in `.gitignore` with English while the ignore surface is being updated.

### Task 5: Validate and deliver

**Files:**
- All changed files.

**Steps:**
1. Run targeted discovery and built-in tests.
2. Run `python -m pytest -q`, Context Eval, Task Eval, and `compileall`.
3. Build wheel and source distribution in a temporary directory and inspect metadata for the new dependency.
4. Run `git diff --check`, inspect the complete diff, and scan for secrets and accidental generated files.
5. Commit only after every required check passes.
6. Push `feature/llmignore-discovery`, open a ready PR, wait for required checks, and merge through protected `main` when clean.
