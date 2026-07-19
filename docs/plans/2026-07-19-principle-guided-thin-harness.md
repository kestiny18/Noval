# Principle-Guided Thin Harness Implementation Plan

**Goal:** Align Noval's runtime behavior, architecture record, public documentation, GitHub automation, and roadmap around a strong-model, thin-harness doctrine.

**Architecture:** The model owns task strategy and method selection. Noval owns capability exposure, authority, execution semantics, durable state, and externally checkable validation. English documentation is canonical; Chinese remains a first-class translated entry point without duplicating every historical implementation note.

**Tech Stack:** Python 3.10+, Markdown, GitHub Actions, GitHub Issue Forms, pytest.

---

### Task 1: Record the architecture doctrine

**Files:**
- Create: `PHILOSOPHY.md`
- Create: `PHILOSOPHY.zh-CN.md`
- Create: `docs/adr/0004-principle-guided-thin-harness.md`
- Create: `docs/adr/README.md`
- Replace: `DESIGN.md`
- Preserve: `DESIGN.zh-CN.md`

**Steps:**
1. State the model/harness responsibility split and its hard invariants.
2. Distinguish soft operating principles from enforced runtime policy.
3. Document alternatives, failure modes, non-functional constraints, and migration consequences.
4. Mark the historical Chinese decision ledger as non-normative and link it from the canonical design.
5. Verify internal Markdown links.

### Task 2: Make the runtime principles domain-neutral

**Files:**
- Modify: `noval/agent.py`
- Modify: `tests/test_agent.py`

**Steps:**
1. Replace the coding-specific default prompt with concise method-selection principles.
2. Preserve the hard distinction between user intent and tool permission.
3. Require claims to match evidence while avoiding mandatory planning or tool use.
4. Add prompt contract tests that reject Git-specific workflow policy in the generic kernel.
5. Run `python -m pytest -q tests/test_agent.py` and expect all tests to pass.

### Task 3: Rebuild the public documentation surface

**Files:**
- Replace: `README.md`
- Create: `README.zh-CN.md`
- Replace: `CONTRIBUTING.md`
- Create: `CONTRIBUTING.zh-CN.md`
- Replace: `AGENTS.md`
- Create: `AGENTS.zh-CN.md`
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

**Steps:**
1. Make English the canonical repository and package entry point.
2. Lead with the thin-harness thesis rather than a feature inventory.
3. Keep capability claims bounded by the current v0.10 implementation.
4. Provide prominent reciprocal language links.
5. Update package metadata and contributor guidance.

### Task 4: Professionalize GitHub collaboration and automation

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/ISSUE_TEMPLATE/bug_report.yml`
- Create: `.github/ISSUE_TEMPLATE/feature_request.yml`
- Create: `.github/ISSUE_TEMPLATE/architecture_proposal.yml`
- Create: `.github/ISSUE_TEMPLATE/config.yml`
- Create: `.github/pull_request_template.md`
- Create: `.github/dependabot.yml`
- Create: `.github/workflows/security.yml`
- Create: `.github/workflows/release-check.yml`
- Create: `SECURITY.md`

**Steps:**
1. Give CI steps explicit names, timeouts, compilation checks, and a package-build smoke job.
2. Add English-first structured issue and pull-request templates.
3. Add dependency maintenance and a responsible security-reporting policy.
4. Avoid adding publication workflows that imply PyPI ownership or unattended releases.
5. Validate YAML parsing locally.

### Task 5: Validate and deliver

**Files:**
- All changed files.

**Steps:**
1. Run targeted tests for the operating-principle contract.
2. Run `python -m pytest -q` and expect the full suite to pass.
3. Run `python -m compileall -q noval examples evals`.
4. Build the source distribution and wheel in a temporary output directory.
5. Run `git diff --check`, inspect `git diff --stat`, and scan changed files for secrets.
6. Commit and push only after every check passes.
7. Update Issues #1, #3, #4, and #5; keep issues open where acceptance criteria remain.
8. Create one focused follow-up issue for goal/evidence/completion contracts rather than claiming that architecture is implemented.
