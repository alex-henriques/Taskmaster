# Contributing

How the two of us work on this repo. The design is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). What Person A (core) and Person B (interface) agree on is in [docs/CONTRACTS.md](docs/CONTRACTS.md).

Each of us works on their own branch: `viceda-s` (core) on `morcão`, and `alex-henriques` (interface) on `morcona`.

## Rules

1. `main` always works. Nobody pushes to `main`: every change goes through a pull request that the other person reviews.
2. Work reaches `main` through a pull request from your own branch. Its description has one `Closes #N` line for each issue it finishes, so the merge closes them and unblocks the issues that wait for them.
3. Only take an issue whose blockers are all closed, and merge `main` into your branch first to get their code. The bonus (#14 to #19) only starts once #13, the defense rehearsal, is closed: the bonus only counts if the mandatory part is flawless.
4. `docs/CONTRACTS.md` only changes when both of us agree. Talk first, then change it in the same PR as the code.
5. Python 3.10 or newer, with the standard library plus PyYAML and nothing else (ruff, in `requirements.txt`, is only a tool for the commit hook and is never imported). Tests use `unittest`. Every module starts with `from __future__ import annotations`.
6. In the defense, each of us must explain the other's code. Review to understand it, not only to approve it.
7. No AI `Co-Authored-By` trailers in commit messages.

## One-time setup

```bash
git clone git@github.com:alex-henriques/Taskmaster.git && cd Taskmaster
python3 --version          # 3.10 or newer
make install               # .venv with PyYAML and ruff, and turns on the pre-commit hook
gh auth login              # GitHub CLI, for issues and pull requests from the terminal
```

## The loop for each issue

### 1. Pick an issue

```bash
gh issue list --assignee @me            # what I am working on
gh issue list --label core              # Person A's issues (Person B: --label interface)
gh issue view 5 --web                   # read it; "Blocked by" is in the sidebar
gh issue edit 5 --add-assignee @me      # take it
```

The open issues that nothing blocks any more:

```bash
for n in $(gh issue list --state open --json number --jq '.[].number'); do
  open=$(gh api "repos/alex-henriques/Taskmaster/issues/$n/dependencies/blocked_by" --jq '[.[] | select(.state == "open")] | length')
  [ "$open" = 0 ] && gh issue view "$n" --json number,title --jq '"#\(.number) \(.title)"'
done
```

### 2. Bring your branch up to date

```bash
git switch morcão                              # alex-henriques: morcona
git fetch origin && git merge origin/main      # what the other person merged; never rebase a pushed branch
```

Do this before you start an issue, and each time a pull request from the other person is merged.

### 3. Work, test, commit

```bash
make test                                                   # every test
.venv/bin/python3 -m unittest tests.test_process -v         # one file
.venv/bin/python3 -m unittest tests.test_process.ProcessTest.test_backoff   # one test (example names)
make run CONFIG=demo/v1.yaml                                # the program
.venv/bin/ruff check --fix . && .venv/bin/ruff format .     # when the pre-commit hook blocks a commit
git add -p                                                  # choose what goes into the commit
git commit -m "feat(core): spawn programs with Popen (#5)"
```

Use `.venv/bin/python3`, not the system `python3`: PyYAML is only installed in `.venv`. The pre-commit hook runs `ruff check` and `ruff format --check`. CI only runs the tests, so the hook is the only lint check.

Commit messages follow `type(scope): what changed`.
- Types: `feat`, `fix`, `test`, `docs`, `refactor` and `chore`.
- Scopes: `core`, `shell`, `docs` and `ci`.
- Keep commits small.

### 4. Open the pull request

```bash
git push
gh pr create --base main --head morcão --reviewer alex-henriques   # alex-henriques: --head morcona --reviewer viceda-s
gh pr checks                                                       # CI must be green
```

- `gh pr create` asks for a title and opens the PR template for the description. Add one `Closes #N` line for each issue the PR finishes.
- A branch can only have one open pull request to `main`. If yours is still open, push more commits to it and add their `Closes #N` lines, instead of opening another one.

### 5. Review the other person's pull request

```bash
gh pr list
gh pr checkout 23                             # run it on your own machine
make test
gh pr review 23 --approve                     # or: --request-changes --body "what, and why"
```

### 6. Merge, then both update

```bash
gh pr merge 23 --merge                        # the author merges, after the approval
git fetch origin && git merge origin/main     # both of us, each on our own branch
```

- Always `--merge`, never `--squash`. Your branch lives on after the merge. With a squash, the next pull request from it would show the same changes again, and could conflict.
- Never `--delete-branch`: the branch is yours for the whole project.

## Changing a contract

1. Tell the other person what you want to change in `docs/CONTRACTS.md`, and why.
2. Once you agree, change the contract and the code in the same pull request.
3. Write "contract change" in the PR description, so that the review looks at it first.

## Bonus pull requests

Before any bonus PR (#14 to #19) is merged, `make test` and the whole of `demo/DEFENSE.md` must pass again on the VM.

Tests that need root skip themselves under `make test`. Run them too, with `sudo .venv/bin/python3 -B -m unittest tests.test_process -v`. The `-B` keeps root-owned `.pyc` files out of the repo.

## Looking at supervised processes

```bash
ps -o pid,ppid,pgid,sid,stat,cmd --forest     # the process tree; a Z in STAT is a zombie
pstree -p "$(pgrep -f taskmaster.main)"
kill -HUP "$(pgrep -f taskmaster.main)"       # reload the config
ls -l /proc/<pid>/fd                          # where stdout and stderr go
grep -E 'Umask|Uid|Gid' /proc/<pid>/status    # the umask and the user of a program
tail -f taskmaster.log
```

## Protecting `main` (repo owner)

If the plan allows it (GitHub Pro, or the Student Developer Pack), the owner can enforce rule 1. Go to Settings → Branches and add a rule for `main` with "Require a pull request before merging" and 1 approval.
