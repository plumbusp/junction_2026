# Repository rules

Written for: the team (Julia and the agents). Changed only by team decision.

## 1. Git

- Run git from the repository root with the plain `git` command.
- **Commit, push, merge and deployment happen only after an explicit "yes" in the conversation.** Approval of one commit does not carry over to the next.
- Commit with explicit paths (`git add <path>`), never `git add -A`. This keeps other people's or unfinished files out of the commit.
- Git identity is already set in `.git/config` (Julia Fiklistova). Do not change it.
- Branch `main`. Agents do not commit: their changes stay in the working tree, and a human commits.

## 2. Secrets

- Keys and passwords live in only two places: `.env` in the repository root and the `secrets/` directory (SSH keys). Both are excluded in `.gitignore` and `.dockerignore`.
- Keys are never written into code, docs, agent briefs, logs, commits or chat. If a value is needed in a conversation, name the variable, not the value.
- **Production secrets never come from the repository.** On the VPS, `.env` is created by hand with permissions `600`. The image is built without `.env` (see `.dockerignore`).
- `.env.example` contains only variable names.
- If a key leaks (into a commit, chat or log), reissue it in the Hostinger panel. Deleting the line from the file is not enough: a copy may already be out.
- Check before committing: `git check-ignore -v .env` must show the matching rule.

## 3. Metric honesty

These rules protect the main argument for the jury. A violation is invisible in a demo but invalidates the metrics.

1. `data/eval/` is never changed after freezing and is never used for few-shot examples, prompt tuning or thresholds. Tuning uses `data/dev/`.
2. Dev and test do not overlap. Before adding data, check for duplicates on normalised text.
3. Synthetic headlines (LLM-generated) are allowed only as few-shot examples and for demos, never for evaluation.
4. Player disputes go only into dev, and only after review.
5. Metrics are honest: sample size, confidence intervals, FPR on "no technique". Sources are never merged into one number.
6. The game, Analyze and /metrics call one core. A second, "simplified" classifier is not built.
7. Raw datasets (PTC and others) are not committed until their licence is checked. They live in `data/raw/`.

## 4. Deployment

- Deployment to the Hostinger VPS happens only after the user confirms it.
- Before deploying: tests are green, eval results are up to date, `.env` does not end up in the image, `/health` is verified on the public URL.
- Rollback: previous image, described in the runbook (`docs/DEPLOY.md`, created together with the Dockerfile).

## 5. Language

- **Everything in the repository is written in English**: code, identifiers, comments, documentation, agent and skill definitions, test cases and fixtures, commit messages.
- Russian is used only in the chat with the user.
- Existing files must not contain Cyrillic. `invariant-guard` checks this before every commit.

## 6. What lives where

| Path | Role | Change rule |
|---|---|---|
| `docs/PLAN.md` | plan by stage and dataset | by team decision |
| `docs/STATUS.md` | current state, blockers, decision log | after every cycle |
| `docs/ARCHITECTURE.md` | modules and the `classify()` contract | by agreement |
| `docs/KNOWLEDGE_BASE.md` | challenge, jury criteria, techniques, datasets | as the project learns |
| `docs/TOOLS.md` | stack tools and their status | as things change |
| `docs/INFRA.md` | non-secret infrastructure facts | as things change |
| `data/eval/` | frozen test set | read-only after freezing |
| `.env`, `secrets/` | secrets | local only |
