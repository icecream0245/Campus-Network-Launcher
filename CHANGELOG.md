# 更新日志

本文件记录本地迭代分支（基于上游 `Redlnn/Ruijie-ePorta-Tool` v1.6）的改动。

## [1.7.0] - 本地迭代版

### 新增

- **守护模式（`--watch`）**：常驻后台运行，检测到掉线后自动重新认证；网络恢复后自动回到监视状态。
- **保活探测**：`daemon.keepalive_interval` > 0 时，在线状态下定期发起轻量探测，维持链路活跃。
- **失败退避（防封禁）**：登录失败后等待时间按 5s → 10s → 20s → … 指数递增，默认上限 300s；
  可选 `cooldown_after_failures` / `cooldown` 在连续失败后进入更长冷却。
- **校园网环境判断前置**：守护模式下若认证服务器不可达，不会盲目发起登录请求。
- **开机自启管理**：`--install-autostart` / `--remove-autostart`，
  支持「启动目录快捷方式」（默认，无需管理员）与「注册表 Run 项」两种方式。
- **命令行接口**：`--watch`、`--login`、`--logout`、`--status`、`--yes`/`--no`、`--quiet`、
  `--no-toast`、`--dialog`、`--config`、`--log-file`、`--verbose`、`--allow-multiple`、`--iterations`。
- **日志**：默认写入程序目录 `logs/ruijie-eporta.log`，自动轮转（1 MiB × 3 份），日志中不记录密码。
- **离线测试体系**：内置模拟 ePortal 服务器（`tests/mock_eportal.py`）+ 50 余条 pytest 用例，
  覆盖配置兼容性、认证接口、重连状态机、退避策略与自启逻辑。

### 变更

- **重构为 `ruijie_eporta` 包**：拆分为 `config` / `network` / `eportal` / `daemon` / `notify` /
  `autostart` / `logging_setup` / `winutil` / `cli`，核心逻辑不再与界面耦合。
- **移除 `tinyWinToast` 依赖**：Toast 通知改为内置实现（PowerShell 调用 WinRT），
  不再需要从 GitHub 安装 git 依赖。
- **移除 `pywin32` 依赖**：单实例互斥体改用 `ctypes`，快捷方式改用 PowerShell COM。
- **移除 `tkinter`/`pywin32` 相关打包内容**：提示框改用 `ctypes.MessageBoxW`，
  exe 体积由约 10 MB 降至约 5 MB，且不再打包 tcl/tk 的 800+ 个数据文件。
- **不再用 `poetry` 管理**：改为标准 `pyproject.toml`（PEP 621）+ `requirements.txt`，
  `build.ps1` 一键构建（自动建 venv、跑测试、打包）。
- **Python 版本要求放宽**：由 `>=3.9,<3.11` 放宽为 `>=3.9`，在 Python 3.12 上验证通过。
- **认证接口更健壮**：非 JSON 响应、HTTP 错误码、超时、连接失败都会给出明确错误信息，
  不会像上游那样直接抛异常退出。
- **`--logout` 语义修正**：优先使用配置中的 `logout_data`，为空时回退到 `login_data`（兼容上游行为）。
- **同时构建两个可执行文件**：`Ruijie_ePorta_Tool.exe`（无控制台）与
  `Ruijie_ePorta_Tool_Console.exe`（有控制台，便于排查）。

### 兼容性

- 完全兼容上游 v3 配置文件：`funtion` 拼写、`login_data` 字段、`main.version: 3` 均按原样接受。
- 新增的 `daemon` / `network` / `notify` / `autostart` 段落全部可选，缺失时使用内置默认值。
- 不带参数运行时的行为与上游一致（检查校园网 → 已联网询问是否断网 → 未联网则登录）。

### 已知限制

- 仍需学校登录接口不需要图形验证码；配置抓包参数需要用户自行完成。
- 开机自启与 Toast 通知仅实现 Windows 版本。
- 少数受限环境下 PyInstaller 单文件程序解包临时目录可能失败
  （`Could not create temporary directory!`），可用 `--runtime-tmpdir` 指定可写目录后重新打包。

### 验证情况

- 单元测试：`pytest` 71 项全部通过（配置兼容性 / 认证接口 / 探测判定 / 重连状态机 / 退避策略 / 自启逻辑）。
- 端到端自检：`tools/e2e_check.py` 用真实 CLI 对接本地模拟 ePortal，覆盖
  「未联网登录 → 状态查询 → 模拟掉线自动重连 → 断网 → 日志落盘」全流程；
  源码模式与打包后的 exe 两种方式均通过。
- 构建环境：Windows + Python 3.12.10 + PyInstaller 6.22.3，产出两个 exe 各约 9.4 MB
  （相比上游 10.2 MB 且已剔除 tcl/tk 全部资源）。
