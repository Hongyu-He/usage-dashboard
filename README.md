# Codex / Claude Code 用量看板

一个放在开发机上的个人看板：浏览器读取缓存，后台每 15 分钟重新统计本机日志。可以按日期比较 Codex 与 Claude Code 的 API 等效费用、Token、每日／累计趋势、模型构成，并导出当前区间 CSV。

统计由固定版本的 [ccusage](https://github.com/ccusage/ccusage) 完成；本项目只用 Python 标准库和无构建的静态网页，不调用模型 API，也不上传日志。这不是 OpenAI 或 Anthropic 的官方项目。

## 安装

在运行 Codex / Claude Code 的那台机器（通常是远程开发机）上执行：

```bash
git clone https://github.com/Hongyu-He/usage-dashboard.git ~/usage-dashboard
cd ~/usage-dashboard
npm install --prefix .tools --no-audit --no-fund ccusage@20.0.20
chmod +x .tools/node_modules/@ccusage/ccusage-linux-x64/bin/ccusage
.tools/node_modules/@ccusage/ccusage-linux-x64/bin/ccusage --version
python3 control.py start
```

需要 Python 3.10+（仅标准库）。npm 只用于安装 ccusage，运行时直接调用它的原生二进制；npm 包里的原生二进制不带执行权限（官方 Node 启动脚本在首次运行时才补上），所以要手动 `chmod +x`。默认配置对应 Linux x64；其他平台或安装位置见[配置](#配置)。

看板就在你面前这台电脑上时，直接打开 http://127.0.0.1:18763/ ；在远程开发机上时，按下一节从本地电脑连接。

## 从本地电脑打开远程看板

以下操作在**你自己的电脑**上进行，不是在开发机的远程终端里。`YOUR_SSH_ALIAS` 换成你平时连接开发机的 SSH Host 别名（即 `ssh 这个名字` 能登录；沿用现有的密钥和跳板配置）。不需要把日志复制到电脑，也不需要本地安装 Python 或 Node。下文假设项目在远程的 `~/usage-dashboard`，放在别处时换成实际路径。

### 推荐：让本地编程助手做成双击打开的 App

本仓库不附带本地 App：各系统做法不同，由你电脑上的编程助手（如 Claude Code、Codex）按你的环境生成更可靠。把下面提示词“我的信息”里的 `YOUR_SSH_ALIAS` 换成你的别名（其余项按需修改），整段发给它。做好后双击图标即可：自动确认远程服务、在后台建立隧道并打开浏览器，不用记命令，也不用保留终端窗口。

```text
请在我这台电脑上做一个双击即可打开的启动器，用来查看远程开发机上的用量看板（https://github.com/Hongyu-He/usage-dashboard）。

我的信息（如果 SSH 别名还是 YOUR_SSH_ALIAS，先问我）：
- SSH 别名：YOUR_SSH_ALIAS（ssh YOUR_SSH_ALIAS 能直接登录）
- 远程项目目录：~/usage-dashboard
- 远程端口：18763
- 本地端口：18763

看板只监听远程的 127.0.0.1，要通过 SSH 本地端口转发访问。双击后：
1. 如果 http://127.0.0.1:<本地端口>/healthz 已返回 JSON 且 service 为 usage-dashboard，直接打开浏览器，不重复建隧道。检查时绕过 HTTP 代理（例如 curl --noproxy '*'）。
2. 否则运行 ssh <SSH 别名> "python3 <远程项目目录>/control.py start"（可重复执行；已在运行时不会启动第二份）。
3. 在后台建立转发 127.0.0.1:<本地端口> → 远程 127.0.0.1:<远程端口>，带上 ExitOnForwardFailure=yes、ServerAliveInterval=30、ServerAliveCountMax=3。用 SSH 控制套接字管理这条连接：已有可用连接就复用；休眠或断网后失效的连接要能识别并重建。
4. 确认 /healthz 返回 usage-dashboard 后，再用默认浏览器打开 http://127.0.0.1:<本地端口>/；本地端口被别的程序占用时报错，不要打开。
5. 不需要保持终端窗口。出错时用系统对话框或通知说明原因（SSH 认证、端口占用、开发机离线等），日志写到启动器自己的目录。SSH 需要密码、MFA 或确认主机密钥时，打开一个终端窗口让我完成认证，不要在后台挂起。

形式与约束：
- macOS 做成 .app（能放桌面、拖进 Dock）；Windows 做桌面快捷方式；Linux 做 .desktop 启动项。尽量只用系统自带工具，不装额外依赖。
- 图标用看板自带的：隧道建立后可从 http://127.0.0.1:<本地端口>/app-icon.icns（macOS）、/favicon.ico（Windows）或 /app-icon.png 下载。
- 另提供关闭连接的方式：只关闭这个启动器建立的隧道，不停止远程服务。
- 只监听 127.0.0.1；不要修改 ~/.ssh/config 或密钥，不要关闭主机密钥校验，不要保存密码，不要用管理员权限；只结束你自己建立的进程和连接，不要按名字杀进程。
- 文件放在用户目录，例如 macOS 的 ~/Library/Application Support/UsageDashboard/、Windows 的 %LOCALAPPDATA%\UsageDashboard\、Linux 的 ~/.local/share/usage-dashboard/。
- 连接和校验逻辑可参考仓库里的 scripts/open-dashboard.sh 和 scripts/open-dashboard.ps1（远程 <远程项目目录>/scripts/ 下也有）。

做完后实际运行一次，确认浏览器能打开看板，再告诉我：文件在哪里、怎么关闭连接、怎么卸载。
```

### 手动：两条命令

在本地终端依次执行（macOS、Linux、Windows PowerShell 都可）：

```bash
ssh YOUR_SSH_ALIAS "python3 ~/usage-dashboard/control.py start"
ssh -NT -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -L 127.0.0.1:18763:127.0.0.1:18763 YOUR_SSH_ALIAS
```

然后在本地浏览器打开 **http://127.0.0.1:18763/**。第二条命令保持不退出、没有输出是正常的，表示隧道在工作。关网页不影响采集；终端按 Ctrl+C 仅断开隧道，远程看板继续运行。

如果本地 `18763` 被占用，把第二条命令中第一个 `18763` 改成 `18764`，浏览器也改为 `http://127.0.0.1:18764/`；右边的远程端口仍用 `18763`。出现 SSH 主机密钥变更警告时先核验主机，不要关闭校验。

### 手动：快捷脚本

macOS / Linux，第一次下载（在你想保存脚本的本地目录执行）：

```bash
scp YOUR_SSH_ALIAS:usage-dashboard/scripts/open-dashboard.sh ./open-dashboard.sh
bash ./open-dashboard.sh YOUR_SSH_ALIAS
```

以后只需第二条。脚本会确认远程服务已启动、建立专属 SSH 隧道、核验接口并打开浏览器。按回车或 Ctrl+C 关闭本次隧道，不停止远程采集。换本地端口：`bash ./open-dashboard.sh YOUR_SSH_ALIAS 18764`。项目不在远程 `~/usage-dashboard` 时，用环境变量指定：`USAGE_DASHBOARD_DIR=/path/to/usage-dashboard bash ./open-dashboard.sh YOUR_SSH_ALIAS`。

Windows PowerShell，第一次下载并运行：

```powershell
scp YOUR_SSH_ALIAS:usage-dashboard/scripts/open-dashboard.ps1 ./open-dashboard.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File ./open-dashboard.ps1 -SshAlias YOUR_SSH_ALIAS
```

项目在其他目录时加 `-RemoteDir /path/to/usage-dashboard`。`ExecutionPolicy Bypass` 只针对这一次脚本进程，不改全局设置；所在组织的策略禁止时直接用上面的两条 SSH 命令。脚本可能弹出 SSH 认证窗口，请完成认证并保持窗口打开。`.ps1` 尚未在 Windows 上实测。

## 使用和统计口径

- 启动时默认显示近 30 天（含今天），并限制在已有数据范围内；“全部”显示配置的起始日期 `since` 至最近采集日，按配置的 `timezone` 切分日期。支持近24小时、近 7 天、近 30 天、本月、全部及自定义区间。
- “近24小时”显示截至最近一次采集开始时刻的滚动24小时用量，按日志时间戳每15分钟分桶；首尾不足15分钟的时段按窗口截取。汇总、迷你图、模型／Token 构成、明细与 CSV 使用同一窗口。其他日期范围仍按天展示。
- 15分钟费用仍由固定版本 ccusage 计价，保留去重、缓存、长上下文和速度档位口径；临时文件只含用量元数据，采集后自动移除。旧快照缺少15分钟数据时会提示刷新，不把未采集时段显示成零。
- 每 15 分钟自动采集；网页每 10 秒查询缓存状态。刷新按钮立即请求一次采集，多窗口不会同时重复扫描。
- 费用是 **按模型 API 价格重算的美元估值**，不是订阅实际账单、套餐剩余额度或供应商后台全量用量。
- 只包含这台机器当前可读取的 Codex / Claude Code 日志；其他设备、已删除日志不在范围内。无日志日期显示“记录到的零”，不证明实际没有使用。
- Token 合计包含输入、输出、缓存读取、缓存写入；推理 Token 已包含在输出里，不重复相加。两种工具的缓存口径会影响 Token 数，不能直接等同工作量。
- 首次采集前显示等待；任何一方采集失败、缺价格或数值对不上，整份新快照都不发布，保留最后一次成功结果并提示错误。失联时页面保留旧画面并自动重连。
- 同一历史日可能因迟到／归档日志、上游解析或价格变化而被重算。每次采集保存原始统计报告；历史 Token 减少／费用明显变化会提示。不能把跨快照总额直接相减当成新增用量。

## 管理服务

在项目目录中执行：

```bash
cd ~/usage-dashboard
python3 control.py status
python3 control.py start
python3 control.py stop
```

`start` 可重复执行，不会启动第二个服务。`stop` 只在接口、项目绝对路径、用户和进程命令行均匹配时停止本项目。修改代码或配置前先停止，修改后再启动。后台进程与 SSH 连接脱离，但**机器关机／重启后不会自动开机自启**；重新运行本地连接命令或快捷脚本即可恢复。机器离线期间不采集，下次会重新扫描整个统计范围。

服务只绑定 `127.0.0.1:18763`，只提供汇总数据和白名单内的网页、图标文件，不提供原始会话内容。不要改成 `0.0.0.0` 或对公网暴露。本地同用户／同主机进程仍可访问此端口；此设计的访问边界是这台机器及其 SSH 账户，不是多用户 SaaS 登录。

### 应用图标

网页已配置浏览器 favicon 和 Apple touch icon。图标采用深蓝圆角底与蓝橙用量柱形，设计源图和生成说明保存在 `assets/icon-design/`，运行时不依赖图像工具。

桌面快捷方式可能保留创建时的图标。可通过当前看板地址的 `/app-icon.png` 下载 1024 px PNG，或通过 `/app-icon.icns` 下载 macOS 图标文件。已有 macOS 快捷方式可在“显示简介”中替换图标；也可以重新创建网页快捷方式。更改本地转发端口时，下载地址沿用该端口。

目录说明：

```text
collector.py         ccusage 采集、校验、快照发布
intraday.py          近24小时事件分桶与 ccusage 计价
server.py            回环 HTTP 服务、后台调度
control.py           安全启停与状态
dist/                网页源码，无构建／外部 CDN
scripts/             电脑端 SSH 连接快捷方式
tests/               统计及缓存／接口回归测试
config.json          默认配置
local-settings.json  可选覆盖配置（不入 Git）
.tools/              npm 安装的固定版本 ccusage（不入 Git）
data/latest.json     最近一次成功的聚合结果（运行后生成）
data/snapshots/      每次采集的压缩报告和来源信息（不含对话正文）
run/server.log       服务和采集日志
```

## 配置

`config.json` 是默认配置。需要改动时，在项目目录新建 `local-settings.json`（不入 Git），只写要覆盖的项，例如：

```json
{
  "since": "2026-08-01",
  "timezone": "America/Los_Angeles",
  "refresh_seconds": 1800
}
```

- `since`：统计起始日期（含当天），默认 `2026-01-01`。修改后立即点击刷新；旧缓存会明确保留自己的范围直至新采集成功。
- `timezone`：按哪个 IANA 时区切分日期，默认 `Asia/Shanghai`。
- `port`：服务端口，默认 `18763`。若更改，SSH 转发目标和快捷脚本的第三个参数（PowerShell 为 `-RemotePort`）也应同步更改。
- `refresh_seconds`：自动采集间隔，默认 900 秒，最少 30 秒。
- `ccusage_binary`：ccusage 可执行文件。不含 `/` 的名字从 `PATH` 查找，相对路径从项目目录起算，也可以写绝对路径。默认是 `.tools` 里 Linux x64 的原生二进制；macOS 等平台改成 `.tools/node_modules/@ccusage/` 下实际存在的对应目录（例如 `ccusage-darwin-arm64`），同样先 `chmod +x` 再用 `--version` 验证。
- `expected_ccusage_version`：固定为 `20.0.20`。不要默默升级版本；版本改变需复核同一历史日期统计和模型价格后再更新。

请指向原生二进制，而不是 `node_modules/.bin/ccusage`：后者是 Node 启动脚本，通过 SSH 启动服务时 `PATH` 里可能没有 `node`。

采集仍需联网获取价格表；离线缺少模型价格时保留旧结果，绝不把缺价当零。

资源：两种工具的日报和15分钟报告串行扫描，ccusage 最多使用 2 个 CPU、低优先级；每个报告超时上限 120 秒，事件分桶也有 120 秒上限。平时只运行轻量 Python 服务。无 GPU、无模型 API 调用。若日志增长导致持续重负载，请降低采集频率并重新评估，不要无限加并发。每次快照完整保留且不自动删除，请留意磁盘配额。

## 检查与故障排查

```bash
python3 -m unittest discover -s tests
node --check dist/app.js
node tests/test_frontend.js
bash -n scripts/open-dashboard.sh
```

与原生 ccusage 逐项对照的集成测试需要已安装的 ccusage 20.0.20，找不到时自动跳过。

- SSH 连接失败：先确认平时的 `ssh YOUR_SSH_ALIAS` 可用；看板脚本不修改 SSH 配置或密钥。
- 转发端口被占用：换本地端口，或关闭自己之前的隧道；不要杀不明进程。
- 页面显示旧数据：查看最近更新时间和错误提示；在服务所在机器查看 `run/server.log`，先修复网络／依赖，再点刷新。
- 提示 `ccusage not found`：确认已按“安装”一节装好 ccusage，或在 `local-settings.json` 设置正确的 `ccusage_binary`。提示 `ccusage is not executable`：对该文件执行 `chmod +x`。
- 机器已重启：重新运行本地快捷脚本，它会重新启动看板。
- 不想继续后台采集：运行 `python3 control.py stop`，文件和历史快照不会删除。

## 验证边界

Python 统计校验／失败保留缓存／调度与 HTTP 接口、前端纯数据函数和语法均有回归检查。近24小时切换、15分钟／累计曲线、Token 指标、CSV、日期切换和手机布局已在 Chromium 中手动验证。Windows 脚本尚未在 Windows 上实测。

支持 `document.modelContext` 的浏览器可使用可选的 `set_usage_date_range` 结构化筛选接口；尚未实测。它不影响普通浏览器控件，也不会启动采集或调用模型。

## 许可证

[MIT](LICENSE)
