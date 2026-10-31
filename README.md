# ctxguard

Read-only growth monitor for the state that silently breaks AI agent apps.

Two failure modes, same shape — something grows a little on every turn or every
launch, nothing ever shrinks it, and one day the app stops opening:

| Failure | What grows | The wall |
|---|---|---|
| **Context blow-up** | a session transcript | the model's context window |
| **Request-header blow-up** | a cookie jar (one login cookie per launch) | the 16 KB HTTP header limit → **431** |

`ctxguard` watches both, warns before the wall, and tells you what happened while
you were away. It never modifies your application's data: the only things it
writes are its own state files under `~/.ctxguard`.

```console
$ ctxguard scan
ctxguard 0.2.0  2026-10-31 15:42:29
  * dsh transcripts (file_size): 26 file(s), 3 alert(s)
  * dsh web cookies (cookie_jar): 63 cookie(s), 14.44 KB, 1 alert(s)

[CRIT] 7.86 MB - session-31df48e9/session.v4.jsonl.zstd  (dsh transcripts)
    C:\Users\me\.dsh\sessions\...\session.v4.jsonl.zstd (limit 5 MB)
[CRIT] 14.44 KB - ...\Network\Cookies  (dsh web cookies)
    63 cookies, 90% of the 16 KB request-header budget; the app may start failing with 431
```

## Why this exists

Both failure modes are invisible until they are fatal:

- **Context**: the turn fails, sometimes the whole session is lost.
- **Cookie header**: the app "won't open". Restarting does not help, reinstalling
  is overkill, and the error (`Web authentication failed`) points at the wrong
  layer entirely. Cookies are not scoped by port, so every past launch's cookie
  is re-sent to today's port until the header is refused.

A size check is cheap, needs no network, and lets you act *before* the wall.

## What it does not do

- It does not delete, archive, rewrite or "optimise" anything it watches.
- It does not talk to any API, model, or cloud service.
- It does not need the network — which matters when the machine that runs your
  agent is the one that is offline.

Cleaning up (deleting cookies, rotating sessions) is intentionally out of scope:
that mutates another application's private state, is version specific, and is far
too easy to get wrong. The README shows how to do it by hand, informed by the
report.

## Install

Requires Python 3.11+ (uses `tomllib`). No third-party dependencies.

```console
git clone https://github.com/<you>/ctxguard
cd ctxguard
pip install -e .
```

Or run it without installing:

```console
PYTHONPATH=src python -m ctxguard.cli scan --config ./my-config.toml
```

## Quick start

```console
ctxguard init                          # writes ~/.config/ctxguard/config.toml
$EDITOR ~/.config/ctxguard/config.toml # point the checks at your files
ctxguard scan                          # one-shot check
ctxguard watch --interval 900          # keep checking every 15 minutes
```

## Concepts

A **check** is one thing that grows. Every check is evaluated independently and
produces **findings** — a level (`attention` / `danger`), a measured value, and
a subject.

Two check kinds are built in:

### `file_size`

Walks a directory and measures matching files. A file is reported when:

1. it is at or above `warn_mb` (default 3 MB), and
2. it was modified within `active_hours` (default 72 h) — stale files cannot
   grow, and
3. it was not reported within `cooldown_hours` (default 12 h), unless it grew by
   at least `growth_mb` (default 0.5 MB) since the last report.

`crit_mb` upgrades the level from attention to danger. On Windows/Chromium it also
applies to Electron.

### `cookie_jar`

Reads a Chromium/Electron SQLite cookie store and estimates the size of the
`Cookie` header it would produce: per cookie, `len(name) + len(value) + 2`.
Encrypted values are counted by blob length minus the `v10` prefix, nonce and
tag — close enough to warn on.

It reports when the estimated header passes `warn_kb` (8), escalates at
`crit_kb` (12), and shows the percentage of `limit_kb` (16).

> The jar is locked for the lifetime of the owning app. While the app is running
> the check reports `!` and is skipped — that is expected, not a failure.
> Run `ctxguard scan` while the app is closed to get a real number.

## Configuration

See [`config.example.toml`](config.example.toml) and `ctxguard init`.

```toml
[[checks]]
kind = "file_size"
name = "dsh transcripts"
root = "~/.dsh/sessions"
pattern = "**/session.v*.jsonl.zstd"

[[checks]]
kind = "cookie_jar"
name = "dsh web cookies"
path = "%APPDATA%/@deepseek-ai/dsh-desktop/Network/Cookies"
warn_kb = 8
```

`~` and environment variables are expanded in both `root` and `path`. Every
`[thresholds]` key can be overridden per check.

### Example: DeepSeek Harness (DSH)

```toml
[[checks]]
kind = "file_size"
name = "dsh transcripts"
root = "~/.dsh/sessions"
pattern = "**/session.v*.jsonl.zstd"

[[checks]]
kind = "cookie_jar"
name = "dsh web cookies"
path = "%APPDATA%/@deepseek-ai/dsh-desktop/Network/Cookies"
warn_kb = 8
crit_kb = 12
limit_kb = 16
```

On Linux/macOS the cookie jar lives under the Electron user-data directory
(`~/.config/<Product>/Network/Cookies` on Linux,
`~/Library/Application Support/<Product>/Network/Cookies` on macOS).

## The "while you were away" report

One line per run and per alert goes to `<state_dir>/events.jsonl`.
`ctxguard report` turns that into a short summary:

```console
$ ctxguard report --hours 24
While you were away:
  from 2026-10-30 18:00 to 2026-10-31 15:42  (21 h 42 min)

Runs    : 84
Alerts  : 2 danger, 3 attention
  danger:
    - session-31df48e9/session.v4.jsonl.zstd  7.86 MB  [dsh transcripts]
    - ...\Network\Cookies  14.44 KB  [dsh web cookies]
Downtime: 2 gap(s), longest 610 min

Verdict: act on the danger items before continuing.
```

No network needed: the log lives on disk, so a power cut does not lose the
history. The `Downtime` line is derived from gaps between runs — a cheap way to
notice that your scheduler stopped firing.

## Scheduling

`ctxguard watch` is a foreground loop; for unattended use, let the OS schedule
`ctxguard scan` instead — a one-shot process cannot get stuck holding a UI.

<details>
<summary>systemd (Linux)</summary>

```ini
# ~/.config/systemd/user/ctxguard.service
[Unit]
Description=ctxguard scan

[Service]
Type=oneshot
ExecStart=%h/.local/bin/ctxguard scan
```
```ini
# ~/.config/systemd/user/ctxguard.timer
[Unit]
Description=Run ctxguard every 15 minutes

[Timer]
OnBootSec=5min
OnUnitActiveSec=15min

[Install]
WantedBy=timers.target
```
</details>

<details>
<summary>Windows Task Scheduler</summary>

```powershell
$action = New-ScheduledTaskAction -Execute "ctxguard.exe" -Argument "scan"
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 15)
Register-ScheduledTask -TaskName "ctxguard" -Action $action -Trigger $trigger
```
Tick "Run whether user is logged on or not" so the task also runs when the
desktop is locked.
</details>

## Cleaning up by hand

When the report says the cookie jar is danger-zone, exit the app completely and
remove the local cookies with a SQLite client (or any browser-cookie tool):

```python
import sqlite3
from pathlib import Path

db = Path.home() / "AppData/Roaming/@deepseek-ai/dsh-desktop/Network/Cookies"
con = sqlite3.connect(db.as_uri() + "?mode=rw", uri=True)
con.execute("DELETE FROM cookies WHERE host_key IN ('127.0.0.1','localhost','::1')")
con.commit()
```

The app writes a fresh cookie on the next launch. Back the file up first — that
is the entire rollback plan.

## Limitations

- **Size is not tokens.** Bytes are a proxy. Compression varies a lot between
  prose, JSON tool output and base64 blobs, so tune `warn_mb` / `crit_mb` against
  your own transcripts. The 3 / 5 MB defaults come from real sessions where
  ~6.6 MB was fatal and 7.9 MB was already archived.
- **Cookie estimates are approximate**: encrypted values lose ~30 bytes of
  overhead, so the number is a good upper-ish bound, not exact.
- Files are inspected with `stat()` and, for cookies, a read-only SQLite query.
  `ctxguard` never parses transcript content, so it cannot tell a discarded tool
  result from a real conversation turn.
- State is local and per-user; there is no cross-machine dedupe.

## Development

```console
pip install -e .
python -m unittest discover -s tests -v
```

## License

MIT. See [LICENSE](LICENSE).

---

## 中文简介

给 AI Agent 做**只读**的增长监控。同一类问题有两个实例：会话文件涨到超过上下文窗口
（那一轮直接失败），以及 cookie 库每次启动多一条登录 cookie、攒到 16 KB 后请求被 431
拒绝（表现为"启动就报 Web authentication failed"，重启无效）。

- **只读**：从不修改、删除、归档任何被监控的数据；只在 `~/.ctxguard` 写自己的状态和告警。
- **零依赖**：Python 3.11+ 标准库即可。
- **多检查项**：`file_size`（转录文件）与 `cookie_jar`（cookie 库）各自独立判定。
- **离线可用**：事件日志落盘，回来后 `ctxguard report` 一条命令看懂。

清理（删 cookie、轮换会话）故意不做——那是改别的应用的私有状态，版本一变就废，
风险远大于收益。看报告，然后手动处理。