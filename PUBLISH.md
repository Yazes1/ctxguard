# 发布清单（GitHub）

仓库已经在 `D:\004\ctxguard`，内容完整、测试通过。只差两步：装 git、推上去。

## 1. 装 git

```powershell
winget install --id Git.Git -e --source winget
# 装完新开一个终端，或刷新 PATH：
$env:Path = [System.Environment]::GetEnvironmentVariable('Path','Machine') + ';' + [System.Environment]::GetEnvironmentVariable('Path','User')
```

## 2. 首次提交并推送

在 GitHub 上先建一个**空仓库**（不要勾选 README / .gitignore / license，否则会冲突），然后：

```powershell
cd D:\004\ctxguard
git init -b main
git add -A
git commit -m "feat: multi-check growth monitor (transcripts + cookie jar)"
git remote add origin https://github.com/<你的账号>/ctxguard.git
git push -u origin main
```

首次推送会要求登录：用 GitHub CLI（`gh auth login`）或 Personal Access Token 都可以。

## 3. 仓库设置建议

**About**（一句话）：

> Read-only growth monitor for AI agent state: warns before a transcript blows the context window, or a cookie jar blows the 16 KB request-header limit.

**Topics**：

```
ai-agents  context-window  observability  monitoring  electron  cookies
python  cli  zero-dependencies
```

**Description 补充**：勾选 Releases、Issues。

## 4. 首个 Release

Tag：`v0.2.0`
标题：`v0.2.0 — two failure modes, one monitor`

正文：

```markdown
ctxguard watches the state that silently breaks agent apps.

Two checks, same shape (grows a little each turn/launch, nothing shrinks it,
one day the app stops working):

- **file_size** — transcript files. Warns at 3 MB, danger at 5 MB, ignores
  stale files, suppresses repeats for 12 h unless the file grew 0.5 MB.
- **cookie_jar** — Chromium/Electron cookie store. Estimates the Cookie header
  size and warns at 8 KB, danger at 12 KB, against the 16 KB HTTP limit that
  produces `431` and looks like "authentication failed".

Also included:

- `ctxguard report` — "while you were away" summary from an append-only local
  event log. No network needed; survives a power cut.
- Zero dependencies (Python 3.11+ stdlib only), MIT licensed.
- 23 unit tests, CI on Linux/macOS/Windows × Python 3.11–3.13.

Read-only by design: it never deletes, archives or rewrites what it watches.
```

## 5. 推送前自检

```powershell
cd D:\004\ctxguard
py -m unittest discover -s tests -v      # 23 passed
git status --short                       # 不应出现 .demo/ 、tests/_tmp/ 、__pycache__/
```

`.gitignore` 已经排除 `__pycache__/`、`*.egg-info/`、`.demo/`、`tests/_tmp/`。

## 6. 之后可做

- 发 PyPI：`py -m build && py -m twine upload dist/*`（需先注册包名 `ctxguard`）
- 加一个 `--notify-webhook`（Slack/飞书/企业微信），但会引入网络依赖，谨慎
- 把 `cookie_jar` 的 `path` 默认值做成按平台推断，省掉一项手填