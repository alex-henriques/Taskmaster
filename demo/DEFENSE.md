# Defense script

Everything the grading sheet asks for, in the order we show it. Each step has
**do** (what to type), **show** (what proves it) and, where it matters, **say**
(what to tell the grader).

Both of us run this whole file on the VM, from a fresh clone, before a bonus PR
is merged. A single failure means we fix it and start again from the top.

## 0. Setup

Three terminals, all in the repo root.

- **T1** runs taskmaster and is where we type shell commands.
- **T2** is the observer: `ps`, `/proc`, the log, `kill`.
- **T3** shows the log all the time: `tail -f taskmaster.log`.


```bash
# T2, once
git clone <repo-url> taskmaster-defense && cd taskmaster-defense
make install
make test                              # all green
pgrep -af -- '-m taskmaster\.main'     # prints nothing: no test left a taskmaster behind

# a working copy of the config, in demo/ so relative paths keep working
cp demo/v1.yaml demo/live.yaml
rm -f taskmaster.log /tmp/taskmaster-*.out /tmp/taskmaster-*.err
```

> Always start taskmaster from the repo root. `cmd` paths such as
> `demo/programs/forever.sh` are relative to where it runs, and `workingdir`
> and `stdout` are relative to the config file's folder, which is why the
> working copy lives in `demo/`.

```bash
# T1
.venv/bin/python3 -m taskmaster.main demo/live.yaml
```

> This is what `make run CONFIG=demo/live.yaml` runs. We type it directly
> because `make` shares T1's terminal: it also gets the `Ctrl-C`s of §8.5, and
> then exits with 130 instead of 0 when taskmaster quits.
>
> Always leave taskmaster with `quit`. If T1's window is closed by mistake, the
> shell sees the lost terminal and shuts down as for `quit`: the programs are
> stopped and taskmaster exits, so just start it again.

```bash
# T3, keep this open for the whole session
tail -f taskmaster.log
```

## 1. Preliminaries

**do** (T2)

```bash
cat author
cat requirements.txt             # PyYAML only
grep -rhE "^(import|from) " taskmaster | sort -u    # stdlib and yaml only
python3 --version
```

**show**
- The author file has both logins (`viceda-s`, and `alehenri`).
- `requirements.txt` has only PyYAML (ruff is dev only; say so if it's there).
- Imports are standard library and `yaml`.

**say** "Nothing crashes during this session, and the log has no traceback."
At the very end we run `grep -ci traceback taskmaster.log` and expect `0`.

## 2. Control shell

**do** (T1)

```
help
status
stop forever_00
status
start forever_00
status
restart forever_00
status
restart forever_00 forever_01
status
start nope
```

**show**
- `help` lists `status`, `start`, `stop`, `restart`, `reload`, `shutdown`, `help`, `quit`.
- `stop` goes to STOPPED, `start` to STARTING and then RUNNING, `restart` gives a
  new pid (compare the pid column before and after).
- `start nope` gives a readable error (`no such process: nope`), not a traceback.
- Tab completion works on command names and program names, and `↑` brings back
  the previous command.

**say**, if the grader types `start all`: `echo` ends FATAL. `echo.sh` reads its
stdin, which taskmaster sets to `/dev/null`, so it exits at once, before its
`starttime`.

## 3. Config file

**do** (T1) `status`

**show** every program from `demo/v1.yaml` is listed:
`crash echo exit_expected exit_unexpected flood forever_00 forever_01 stubborn whoami`.
`forever` (`numprocs: 2`) is `forever_00` and `forever_01`. Programs with
`autostart: true` are `forever` (RUNNING) and `crash`, which went BACKOFF for
about 3 s right after startup and is FATAL by now. All the others are
`STOPPED  Not started`.

## 4. Logging

Look at the log in T3 and show one line for each event as we produce it during the
next sections. The full list to point at:

| Event | Where we produce it | Level | What the log line looks like |
|---|---|---|---|
| start | §2 | INFO | `forever_00: STOPPED -> STARTING (spawn, attempt 1)`, then `STARTING -> RUNNING (alive after 1.0 s)` |
| stop | §2 | INFO | `forever_00: RUNNING -> STOPPING (stop, sent SIGINT)`, then `STOPPING -> STOPPED (exit SIGINT)` |
| restart | §2 | INFO | `forever_00: restart` |
| unexpected death | §6.2 and §7 | WARNING | `exit_unexpected: RUNNING -> EXITED (exit 3, unexpected)` |
| SIGKILL after `stoptime` | §6.4 | WARNING | `stubborn: killed after stoptime` |
| FATAL after too many retries | §6.3 | WARNING | `crash: STARTING -> FATAL (exited early with 1; gave up)` |
| reload | §5 | INFO | `reload: added [...], changed [...], removed [...]` (`received SIGHUP` before it, with `kill -HUP`) |
| invalid reload | §8.3 | ERROR | `reload failed, nothing changed: <reason>` |

**do** (T3, or T2) `cat taskmaster.log` once now, and again at the end.
 
**show** timestamps, levels, program names and, for an exit, the exit code or the signal.

## 5. Hot reload

We go from `v1.yaml` to `v2.yaml`:

| Program | v1 → v2 | Expected |
|---|---|---|
| `forever` | `numprocs: 2` → `3` | changed: restarted, `forever_02` appears |
| `exit_unexpected` | removed | removed |
| `hello` | new | added (`autostart: false`) |
| everything else | identical | **untouched: same pid** |

**do** (T1): start two programs that stay up, so we have untouched pids to compare.

```
start whoami
start stubborn
status
```

Write down the pids of `whoami` and `stubborn`. Both must be RUNNING.

**do** (T2): swap the config and reload with the shell command.

```bash
cp demo/v2.yaml demo/live.yaml
```

```
# T1
reload
status
```

**show**
- The reply is `added: hello, changed: forever, removed: exit_unexpected`.
- `whoami` and `stubborn` have **the same pids** as before.
- `forever_00..02` have new pids, and `exit_unexpected` is gone.
- The log has the reload summary line.

**do**: now the signal path, going back to v1.

```bash
# T2
cp demo/v1.yaml demo/live.yaml
kill -HUP $(pgrep -f -- '-m taskmaster\.main')
```

```
# T1
status
```

**show** the same behaviour: `whoami` and `stubborn` keep their pids, `forever` goes
back to 2 instances, `exit_unexpected` is back, `hello` is gone. The log has a
second reload line.

**say** "Unchanged programs are never touched: 'unchanged' means the normalized
config is equal. A changed program is stopped and then re-added. If it's still
stopping when we reload again, the new one waits in `pending`, so we never lose a
child."


### 5b. The slow stop case (optional, but be ready to explain it)

`stubborn` ignores `TERM` and is only killed after its `stoptime`. To get a
window that is easy to hit by hand, we give it `stoptime: 30`, and prepare both
files first, so each step is a single `cp`.

**do**

0. T2: prepare the two files (the live config is v1 again at this point).

   ```bash
   sed 's/stoptime: 3/stoptime: 30/' demo/v1.yaml > /tmp/with.yaml
   sed '/^  stubborn:/,/^$/d' /tmp/with.yaml > /tmp/without.yaml
   ```

1. T2: `cp /tmp/with.yaml demo/live.yaml`. T1: `reload`, which answers
   `added: none, changed: stubborn, removed: none`. The old `stubborn` is
   stopped with its old `stoptime: 3`, then re-added. Wait until
   `status stubborn` says STOPPED (about 3 s).
2. T1: `start stubborn`, and wait for RUNNING. T2: `pgrep -f stubborn.sh`, and
   write the pid down.
3. T2: `cp /tmp/without.yaml demo/live.yaml`. T1: `reload` (`removed: stubborn`).
   The 30 s start now.
4. T2: `cp /tmp/with.yaml demo/live.yaml`. T1: `reload` again (`added: stubborn`).
5. T1: `status`, then `start stubborn exit_expected`.

**show**
- After step 3 and after step 4, `status` has a single `stubborn  STOPPING`
  line (`status` shows no pid while stopping), and `pgrep -f stubborn.sh` in T2
  still prints the one pid from step 2: no duplicate.
- `start stubborn exit_expected` answers, in name order,
  `exit_expected: starting` and `stubborn: ERROR (busy: reload pending)`: the
  others still start.
- When the old process is finally killed, 30 s after step 3, `stubborn` shows up
  again as **STOPPED** (its config has `autostart: false`), and
  `pgrep -f stubborn.sh` prints nothing: no orphan.

Wait for that STOPPED before §6, or §6's reload finds `stubborn` still stopping
and `start stubborn` answers busy.

## 6. Every option, with its proof

Reset to v1 first: `cp demo/v1.yaml demo/live.yaml`, then `reload`.

### 6.1 `cmd`, `numprocs`, `autostart`

**show** (T1 `status`): `forever_00` and `forever_01` are RUNNING because
`autostart: true` and `numprocs: 2`, each with its own pid. `exit_unexpected`,
`flood` and `echo` are `STOPPED  Not started`, because `autostart: false`.
`whoami` is RUNNING only because we started it by hand in §5.

### 6.2 `autorestart` and `exitcodes`

| Program | Config | What we expect |
|---|---|---|
| `forever` | `autorestart: always` | restarted if killed (§7) |
| `stubborn` | `autorestart: never` | killed with `kill -9`: ends EXITED and stays there, not restarted |
| `exit_expected` | `unexpected`, `exitcodes: [0, 2]` | exits with 2 after 3 s: **expected**, not restarted |
| `exit_unexpected` | `unexpected`, `exitcodes: 0` | exits with 3 after 3 s: **unexpected**, restarted |

**do** (T1)

```
start exit_expected
start exit_unexpected
start stubborn
status                     # wait ~5 s, then again; note the pid of stubborn
```

```bash
# T2
kill -9 <pid of stubborn>
```

```
# T1
status
```

**show**
- `exit_expected` ends EXITED (code 2) and stays there. The log line is INFO.
- `exit_unexpected` is started again and again. The log shows a WARNING per
  unexpected exit with `exit 3, unexpected`, and a new pid each time.
- `stop exit_unexpected` ends the loop.
- `stubborn` ends EXITED, with `exit SIGKILL, unexpected` in the log, and **stays
  there**, because its `autorestart` is `never`.

### 6.3 `starttime` and `startretries`

`crash` has `starttime: 2`, `startretries: 2`, and exits at once with code 1.

**do** (T1) `start crash`, then `status` a few times.

**show**
- It goes STARTING → BACKOFF → STARTING, with two retries after the first
  attempt, then **FATAL**. (The whole sequence takes about 3 s: a retry after 1 s, then after 2 s)
- The log has one line per attempt and a WARNING when it gives up.
- `forever` (`starttime: 1`) is the proof that `starttime` is honoured: in §6.4
  it stays STARTING for about 1 s, then RUNNING.

### 6.4 `stopsignal` and `stoptime`

- `forever` uses `stopsignal: INT`.

  **do** `stop forever_00`. **show** it stops at once (STOPPED within the
   second; about 0.2 s in our run), and the log says `stop, sent SIGINT`.

  **do** `start forever_00`, then `status` at once and again after 1 s.
  **show** STARTING, then RUNNING: that is `starttime: 1` (§6.3). Leave it
  running, because §6.5 to §7 need its pid.
- `stubborn` uses `stopsignal: TERM`, ignores it, and has `stoptime: 3`.

  **do** `start stubborn`, then `stop stubborn`, and watch `status`.
  **show** STOPPING for about 3 s, then STOPPED. The log has a WARNING that
  `SIGKILL` was sent after `stoptime`.

### 6.5 `stdout` and `stderr`: a file, and discarded

`forever` writes to `/tmp/taskmaster-forever.out` and `.err`. `flood` has
`stdout: /dev/null`. `stubborn` has no `stdout`, so its output is discarded.

**do** (T1)

```
start stubborn
status                     # note the pids of forever_00 and stubborn
```

(`stubborn` was stopped in §6.4, so it has to be started again here.)

**do** (T2), with the pids from `status`

```bash
ls -l /proc/<pid of forever_00>/fd/1 /proc/<pid of forever_00>/fd/2
tail -3 /tmp/taskmaster-forever.out
ls -l /proc/<pid of stubborn>/fd/1 /proc/<pid of stubborn>/fd/2
```

**show**
- `forever_00`: fd 1 → `/tmp/taskmaster-forever.out`, fd 2 →
  `/tmp/taskmaster-forever.err`, and the `.out` file has lines `forever: N`.
- `stubborn`: fd 1 and fd 2 → `/dev/null`.


### 6.6 `env`, `workingdir` and `umask`

`whoami` has `env`, `workingdir: programs`, `umask: 077`, and its output goes to
`/tmp/taskmaster-whoami.out`. `forever` has `umask: "022"` and `STARTED_BY` in
its `env`.

**do** (T1)

```
start whoami               # if it isn't running
status                     # note the pids of whoami and forever_00
```

**do** (T2), with the pids from `status`

```bash
head -3 /tmp/taskmaster-whoami.out
grep Umask /proc/<pid of whoami>/status
ls -l /proc/<pid of whoami>/cwd
tr '\0' '\n' < /proc/<pid of whoami>/environ | grep -E 'GREETING|ANSWER'

grep Umask /proc/<pid of forever_00>/status
tr '\0' '\n' < /proc/<pid of forever_00>/environ | grep STARTED_BY
```

**show**
- The output file starts with `id`, the working directory (`.../demo/programs`)
  and `umask` (`0077`).
- `/proc/<pid>/status` says `Umask: 0077` for `whoami`, and `cwd` points to
  `demo/programs`.
- `forever_00` says `Umask: 0022` and has `STARTED_BY=taskmaster`.

`whoami.sh` ends with `exec sleep 3600`, so it keeps the pid taskmaster
started, but its command line becomes `sleep 3600`. That's why we use the pid
from `status` and not `pgrep -f whoami.sh`.


## 7. The grader's own tests

**kill -9 on a supervised process**

```
# T1
status                       # note the pid of forever_00
```

```bash
# T2
kill -9 <pid of forever_00>
```

```
# T1
status
```

```bash
# T2: no defunct child of taskmaster
ps -o stat,pid,cmd --ppid $(pgrep -f -- '-m taskmaster\.main') | grep '^Z'     # prints nothing
```


**show** `forever_00` is back with a **new pid** (`autorestart: always`). The log
has a WARNING for the unexpected exit (killed by signal 9) and a new spawn.

**crash.sh ends FATAL**: shown in §6.3. Restate it: `crash` is FATAL after the
configured number of attempts, and the log says it gave up.

## 8. "Reasonable" abuse

### 8.1 flood

**do** (T1) `start flood`, and for 30 s keep using the shell: `status`, `help`,
`status forever_00`. In T2 run `top -p $(pgrep -f -- '-m taskmaster\.main')`.

**show** every command answers right away, and taskmaster's CPU stays low,
because taskmaster never reads its programs' output: each child writes straight
to its own file, here `/dev/null`, so there is no pipe for the core to drain.
Then `stop flood`.

**say** "The core never blocks. A program that floods output can't slow it down."

### 8.2 Quit with programs running

**do** (T1) make sure `forever`, `whoami` and `stubborn` are running, then `quit`.

**show**
- taskmaster exits by itself after about 3 s, with the shell prompt back, and the
  terminal still in a sane state (type something to check). **say** the 3 s are
  `stubborn`: it ignores `TERM`, so it is killed after its `stoptime`.
- `ps aux | grep -E "forever|stubborn|sleep 3600" | grep -v grep` has no
  leftover programs. The log has "shutdown request" and "taskmaster stopped".
- `echo $?` is `0`.

Start it again afterwards: `.venv/bin/python3 -m taskmaster.main demo/live.yaml`.

### 8.3 Invalid config

**At startup**

```bash
# T2
printf 'programs:\n  x:\n    cmd: sleep 100\n    bogus: 1\n' > /tmp/bad.yaml
.venv/bin/python3 -m taskmaster.main /tmp/bad.yaml; echo "exit code: $?"
.venv/bin/python3 -m taskmaster.main /nonexistent.yaml; echo "exit code: $?"
```

**show** an error on stderr that names the full path (`programs.x.bogus: …`), exit
code `1`, no traceback and nothing started.

**On reload**

```bash
# T2
cp demo/live.yaml /tmp/good.yaml
printf 'programs: [\n' > demo/live.yaml
```

```
# T1
reload
status
```

```bash
# T2
cp /tmp/good.yaml demo/live.yaml
```

**show** `reload` answers `ERROR (invalid config, nothing changed: …)`, every
program keeps running with the same pid, and the log has an **ERROR** line. Then
`reload` again with the good file to show taskmaster recovers.

A YAML syntax error, like the one above, shows YAML's own multi-line message. A
bad field shows the short form, for example `programs.x.bogus: unknown field`.

### 8.4 kill -9 on taskmaster, then start it again

**do**

```bash
# T2
kill -9 $(pgrep -f -- '-m taskmaster\.main')
ps aux | grep -E "forever.sh|stubborn.sh|sleep 3600" | grep -v grep
```

**show** the two `forever.sh` are still alive (since the restart in §8.2 only
`forever` runs; `crash` is FATAL). Re-adopting orphans is a documented
non-goal, and `supervisord` doesn't do it either.

T1's terminal is left without echo: type `reset` and press Enter before going on.

Clean up and restart:

```bash
# T2: kills every demo program on this machine: fine on the VM, not on a shared machine
pkill -f 'demo/programs/' ; pkill -f 'sleep 3600'
```

```bash
# T1
.venv/bin/python3 -m taskmaster.main demo/live.yaml
```

**show** taskmaster starts normally, `status` is correct, and the log file is
appended to, not overwritten.

**say** "Without the cleanup, `forever` would run twice: the old orphans and the
new instances. We say so openly, and `status` only ever reports what taskmaster
itself started."

### 8.5 Ctrl-C spam

**do** (T1) at the prompt, press `Ctrl-C` 20 times in a row, with and without a
half-typed line, then `status`.

**show** taskmaster is still there, `forever_00` and `forever_01` still RUNNING
with the same pids, and nothing happens at the prompt: no new prompt, and a
half-typed line stays as it was (`Enter` runs it, `Ctrl-U` clears it).

**say** the core gets the `SIGINT` through its own handler and ignores it while
there is a shell. The children are in their own session (`start_new_session`),
so the terminal's `Ctrl-C` never reaches them.

### 8.6 A `cmd` that does not exist

**do**

```
# T1
quit
```

```bash
# T2
cat > demo/ghost.yaml <<'EOF'
programs:
  ghost:
    cmd: "/does/not/exist"
    autostart: true
    startretries: 2
    starttime: 1
EOF
```

```
# T1
.venv/bin/python3 -m taskmaster.main demo/ghost.yaml
status                     # wait about 3 s, then again
start ghost
status
quit
```

**show**
- `ghost` goes BACKOFF and ends **FATAL** after its retries.
- The log has a line such as `ghost: STARTING -> BACKOFF (spawn failed: [Errno 2] No such file or directory: '/does/not/exist'; retry in 1 s)`, and a WARNING when it gives up.
- `start ghost` answers `ghost: starting`, and the failure shows up again in `status` and the log. There is no traceback anywhere, and the shell stays responsive.

Delete `demo/ghost.yaml` afterwards. The main taskmaster can be started again with `.venv/bin/python3 -m taskmaster.main demo/live.yaml` if the next steps need it.

## 9. README

**do** (T2) `cat README.md`

**show** every config option is documented, with its default and an example:
`cmd`, `numprocs`, `autostart`, `autorestart`, `exitcodes`, `startretries`,
`starttime`, `stopsignal`, `stoptime`, `stdout`, `stderr`, `env`, `workingdir`,
`umask`, and the bonus `user`. Also the signals (`SIGHUP` reloads, `SIGTERM` and `SIGQUIT` shut down).

## 10. Bonus

Only once everything above has passed: the bonus only counts if the mandatory
part is flawless.

### 10.1 Alerts through syslog

Since §0, every WARNING and ERROR has also gone to syslog, not only to
`taskmaster.log`.

**do** (T2)

```bash
journalctl -t taskmaster --since today --no-pager
journalctl -t taskmaster --since today -p info..info --no-pager     # -- No entries --
```

**show**
- The session's alerts are all there: `crash` and `ghost` FATAL, the unexpected
  exits of `exit_unexpected` and of the `kill -9`s, `stubborn: killed after
  stoptime`, and the invalid reload of §8.3.
- Not one INFO line: no spawn, no `-> RUNNING`, no reload summary.

**do** (T3) `Ctrl-C` the `tail`, then keep this open for §10.2:

```bash
journalctl -t taskmaster -f
```

**say** "It is a second handler on the same logger, at WARNING, with
`ident = "taskmaster: "`, which is what `-t` filters on. Without `/dev/log`,
taskmaster keeps running and writes one warning to its own file; a unit test
covers that case."

### 10.2 Run a program as another user

`demo/bonus.yaml` runs `whoami.sh` as `nobody`, next to a plain `forever`.
Switching users needs root, so we run it twice: without `sudo`, then with it.

**do** (T2) `nobody` must be able to reach the scripts inside our home:

```bash
stat -c %a ~               # 750 or 700: nobody can't get in
chmod o+x ~                # others may go through it, not list it; undone in §11
```

**do** (T1) first without root (`quit` the running taskmaster first, if any):

```
.venv/bin/python3 -m taskmaster.main demo/bonus.yaml
status
quit
```

**show**
- `nobody` is FATAL with `user switching needs root`, and `forever` is RUNNING.
- T3 shows the FATAL line as it happens.
- T2: `ls /tmp/taskmaster-nobody.out` says `No such file or directory`: the
  user is checked before any output file is created.

**do** (T1) now as root:

```
sudo .venv/bin/python3 -B -m taskmaster.main demo/bonus.yaml
status                     # note the pid of nobody
```

**do** (T2)

```bash
head -1 /tmp/taskmaster-nobody.out
ps -o user,pid,cmd -u nobody
grep -E '^(Uid|Gid|Groups)' /proc/<pid of nobody>/status
ls -l /tmp/taskmaster-nobody.out
```

**show**
- `uid=65534(nobody) gid=65534(nogroup) groups=65534(nogroup)`.
- `ps` shows `nobody … sleep 3600`. In `/proc`, the Uid, Gid and Groups lines
  only have `65534`: root's group `0` did not leak.
- The output file belongs to root: taskmaster opens it before the switch, and
  the child inherits the open file.

**say**
- "As root, `Popen` gets `user`, `group` and `extra_groups`, so the child calls
  `setgroups`, `setgid` and `setuid`, in that order, right before `exec`.
  Without root even `setgroups` to yourself fails, so another user is refused
  with a clear error, and your own user is simply ignored."
- If the grader points at `USER=root` and `HOME=/root` further down the file:
  "On purpose, like supervisor: we switch the ids, not the environment."

`-B` stops root from writing `.pyc` files into the clone. `quit` in T1 when done.

## 11. Final Cleanup

```bash
# T2
grep -ci traceback taskmaster.log          # 0
ps aux | grep -E "forever|stubborn|sleep 3600|yes" | grep -v grep   # nothing
find . -path ./.venv -prune -o -user root -print                    # nothing: -B worked
rm -f demo/live.yaml demo/ghost.yaml
sudo rm -f /tmp/taskmaster-*
chmod o-x ~                                # only if §10.2 changed it
git status                                 # clean
```
