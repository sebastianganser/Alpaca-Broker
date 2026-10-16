---
name: session-end
description: Close a work session on Alpaca-Broker - run tests and lint, have the doc-keeper agent update ROADMAP/SESSION_LOG/DECISIONS/LEARNINGS/DATABASE/SCHEDULER/ARCHITECTURE and the CLAUDE.md status, then commit and push. Use at the end of a productive session or work package.
---

# Session End

Follow the "Session End Checklist" in CLAUDE.md. Work autonomously; only ask Sebastian when a documentation fact cannot be verified.

## Steps

1. **State.** `git status` and `git diff --stat`. Identify what changed (code, tests, migrations, docs) and which commits this session already made (`git log --oneline` since the session start).
2. **Verify.**
   - `uv run pytest` must be green (note the test count for the docs).
   - `uv run ruff check <changed .py files>` - no new findings in changed code (the ~154 pre-existing findings are known; do not mass-fix).
   - Frontend touched: `cd frontend && npm run build && npm run lint`.
   - DB isolation: if tests or code paths with `get_session()` changed, run `DB_HOST=127.0.0.1 DB_PORT=1 uv run pytest` (the local `.env` points at production).
3. **Review (only when warranted).** For non-trivial changes to collectors, feature pipeline, targets, migrations or the scheduler that have not been reviewed yet: start the `code-reviewer` agent (Opus) on the diff and resolve Blocker/Major findings. Skip for docs-only changes and small fixes.
4. **Documentation.** Start the `doc-keeper` agent (Agent tool, `subagent_type: doc-keeper`, `model: sonnet`) with a self-contained brief: what was done, commit hashes, decisions made, findings, test count, deploy state, open points, next step. It updates ROADMAP, SESSION_LOG (new entry, German), DECISIONS_*, LEARNINGS, DATABASE, SCHEDULER, ARCHITECTURE and the CLAUDE.md Current Status block as applicable. Then check its result yourself: `git diff` on the docs, numbers against code, relative links resolve, CLAUDE.md still about 200 lines, the "Never Without Explicit Confirmation" list untouched.
5. **Commit.** Stage explicit files (never `.env`; no blind `git add -A` - check `git status` for stray files first). Conventional Commits in English (`feat|fix|test|docs|infra|refactor(scope): subject`), body explains why, plus the Co-Authored-By trailer the harness specifies. Separate commits for code and for docs.
6. **Push.** `git push origin main` (never force). Confirm with `git status` that the branch is up to date with `origin/main`.
7. **Deploy hint.** If code that runs in production changed and is not deployed, say so in the SESSION_LOG entry ("nicht deployt") and tell Sebastian that `/deploy` is pending. Do not deploy unrequested.
8. **Final message to Sebastian (German):** what was done, commits (hashes), test result, what is open, the next step.
