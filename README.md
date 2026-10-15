# Ruijie-ePorta-Tool（本地迭代版 v1.7.0）

基于 [Redlnn/Ruijie-ePorta-Tool](https://github.com/Redlnn/Ruijie-ePorta-Tool) v1.6 的**本地迭代分支**。

上游作者已毕业并停止维护，此分支在其基础上把「跑一次就退出」的脚本重构成**可常驻的守护型工具**：
掉线自动重连、可选保活、一键开机自启，并做了打包与依赖上的现代化改造。
原有的 v3 配置文件**无需任何修改**即可继续使用。

<h4>权责声明（沿用上游）</h4>

- 本程序仅供研究学习之用，无意对锐捷的认证机制做任何抵触性行为
- 本程序不可用于任何商业和不良用途，否则责任自负
- 本程序不保证在任何环境下能够通过，也不保证按时改进，编写及维护纯属个人爱好
- 本程序不保证经过严格测试对机器无害，由于未知的使用环境或不当的使用对机器造成的损害，责任由使用者全部承担

## 简介

通过 Python 标准库 `urllib` 发送 POST 请求，实现锐捷 ePortal Web 认证的登录 / 下线，
并按需常驻后台自动维持在线状态。理论支持所有**登录不需要图形验证码**的学校。

## 相比上游 v1.6 的变化

| 能力 | 上游 v1.6 | 本迭代版 v1.7 |
| --- | --- | --- |
| 运行方式 | 执行一次后退出 | 单次执行 + `--watch` 守护常驻 |
| 掉线恢复 | 需人工/计划任务再次运行 | 自动检测并重连，失败按指数退避（默认 5s→…→300s 上限） |
| 保活 | 无 | `keepalive_interval` 定期探测，维持链路活跃 |
| 开机自启 | 手工放快捷方式到启动目录 | `--install-autostart` / `--remove-autostart` 一条命令完成 |
| 界面依赖 | tkinter（打包进 tcl/tk，体积大） | 原生 MessageBox + Windows Toast，**不打包 tkinter** |
| 第三方依赖 | pywin32、tinyWinToast(git)、PyYAML | **仅 PyYAML** |
| 依赖管理 | poetry，Python `>=3.9,<3.11` | 标准 `pyproject.toml` + `requirements.txt`，Python `>=3.9`（3.12 已验证） |
| 出错处理 | 直接抛异常退出 | 非 JSON / HTTP 错误 / 超时均给出明确提示并记录日志 |
| 可测试性 | 无 | 内置 mock ePortal 服务器 + pytest 用例，离线可验证 |
| 日志 | 无 | `logs/ruijie-eporta.log`，自动轮转，日志中不含密码 |
| 打包产物 | `Ruijie_ePorta_Tool.exe` | 同上 + `Ruijie_ePorta_Tool_Console.exe`（排查用） |

## 快速开始

### 方式一：使用打包好的 exe

1. 把 `Ruijie_ePorta_Tool.exe` 和你的 `config.yml` 放在**同一个目录**；
2. 双击运行：行为与上游一致（检查校园网 → 已联网则询问是否断网 → 未联网则登录）；
3. 安装开机自启（推荐，登录后自动保持在线）：

   ```powershell
   .\Ruijie_ePorta_Tool.exe --install-autostart
   ```

### 方式二：从源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m ruijie_eporta --watch
```

## 命令行用法

```text
Ruijie_ePorta_Tool.exe [选项]
```

| 选项 | 说明 |
| --- | --- |
| 无参数 | 与上游一致的单次模式：判断校园网 → 已联网询问是否断网 → 未联网则登录 |
| `--watch` / `-w` | **守护模式**：常驻后台，掉线自动重连（推荐配合开机自启） |
| `--login` | 只执行一次登录认证，然后退出 |
| `--logout` | 只执行一次断网（下线） |
| `--status` | 打印当前联网状态、配置路径与自启状态（建议用 Console 版查看） |
| `--install-autostart` | 安装开机自启（方式由配置 `autostart.method` 决定） |
| `--remove-autostart` | 卸载开机自启 |
| `--yes` / `--no` | 已联网时直接选择断网 / 不断网，不弹窗（适合任务计划） |
| `--quiet` | 静默：不弹通知，只写日志 |
| `--no-toast` | 不使用 Windows 通知中心消息 |
| `--dialog` | 额外弹出消息框提示 |
| `--config PATH` | 指定配置文件路径（也可用环境变量 `RUJIE_EPORTA_CONFIG`） |
| `--log-file PATH` | 指定日志文件 |
| `--verbose` / `-v` | 输出调试日志 |
| `--allow-multiple` | 允许同时运行多个实例（默认单实例） |
| `--iterations N` | 守护模式最多循环 N 次后退出（调试用） |
| `--version` | 显示版本 |

### 常用组合

```powershell
# 开机自启 + 守护模式 + 静默（最省心的配置）
.\Ruijie_ePorta_Tool.exe --install-autostart

# 只想现在连上，不想弹窗
.\Ruijie_ePorta_Tool.exe --login

# 排查问题：看状态、看日志
.\Ruijie_ePorta_Tool_Console.exe --status --verbose
```

## 配置文件

配置文件名固定为 `config.yml`，放在 exe（或项目根目录）旁边；
新增的 `daemon` / `network` / `notify` / `autostart` 段落**全部可选**，
缺失或删除即使用默认值，因此上游的 v3 配置文件可直接沿用。

程序首次运行若找不到配置文件，会生成一份带注释的模板。

```yaml
# ===== 与上游完全相同的部分（保持不动即可）=====
main:
  version: 3            # 不要修改
funtion:
  check_school_network: true   # 是否检查校园网环境（注意：上游拼写就是 funtion）
  disconnect_network: true     # 单次模式下已联网时是否询问断网
url:
  server: http://172.18.117.201
  login: /eportal/InterFace.do?method=login
  logout: /eportal/InterFace.do?method=logout
cookie: '...'
login_data: { userId: '...', password: '...', ... }
headers: { Referer: '...' }

# ===== 本迭代版新增（可选）=====
daemon:
  check_interval: 30        # 在线时检测间隔（秒）
  keepalive_interval: 0     # >0 时在线也会定期发保活探测
  retry_interval: 5         # 掉线后首次重连等待
  max_retry_interval: 300   # 退避上限，防止被学校封禁
  cooldown_after_failures: 0  # 连续失败 N 次后进入额外冷却（0=关闭）
  cooldown: 180
  login_timeout: 10
  probe_timeout: 3
  log_file: ''              # 留空 = 程序目录 logs/ruijie-eporta.log
  log_max_bytes: 1048576
  log_backup_count: 3
network:
  probes:                   # 联网判定探测地址，按顺序尝试
    - http://connect.rom.miui.com/generate_204
    # 映射写法可校验响应正文，避免被认证门户跳转后的假页面骗过
    - url: http://www.msftconnecttest.com/connecttest.txt
      contains: Microsoft Connect Test
  timeout: 3
notify:
  toast: true               # Windows 通知中心
  dialog: false             # 是否弹消息框（守护模式建议 false）
  quiet: false              # 只通知错误
autostart:
  method: startup           # startup = 启动目录快捷方式；registry = 注册表 Run 项
  args: --watch --quiet     # 自启时附加参数
```

> ⚠️ `config.yml` 内含账号凭据（学号、加密后的密码密文、会话 cookie），请勿上传网盘或分享截图。

## 开机自启

```powershell
.\Ruijie_ePorta_Tool.exe --install-autostart     # 安装（默认写入启动目录快捷方式）
.\Ruijie_ePorta_Tool.exe --remove-autostart      # 卸载
```

- 只影响当前用户，**不需要管理员权限**；
- 想改用注册表方式：在配置中设置 `autostart.method: registry`；
- 想调整自启参数（如加上 `--yes`）：修改 `autostart.args`。

## 构建

需要 Python 3.9+（推荐 3.12）与 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

脚本会自动：创建 `.venv` → 安装依赖 → 运行测试 → 用 PyInstaller 打包，
产物在 `dist\` 目录（`Ruijie_ePorta_Tool.exe` 与 `Ruijie_ePorta_Tool_Console.exe`）。

也可以直接双击 `build.bat`（内部调用同一脚本）。常用参数：

```powershell
.\build.ps1 -Clean          # 清理 build/dist 后重建
.\build.ps1 -SkipTests      # 跳过测试（不推荐）
.\build.ps1 -SkipInstall    # 复用已有虚拟环境
```

## 测试

所有测试都是**离线**的：`tests/mock_eportal.py` 会启动一个本地模拟 ePortal 服务器
（同时提供 `generate_204` 探测端点），验证登录/断网、探测判定与重连状态机。

```powershell
# 单元测试
.\.venv\Scripts\python.exe -m pytest -q

# 端到端自检（真实 CLI 对接本地模拟服务器，含掉线自动重连场景）
.\.venv\Scripts\python.exe tools\e2e_check.py

# 也可以直接验证打包产物（需用 Console 版才能看到 --status 输出）
.\.venv\Scripts\python.exe tools\e2e_check.py --exe .\dist\Ruijie_ePorta_Tool_Console.exe
```

## 已知问题

- 少数受限环境（例如带应用白名单的终端管控软件）下，PyInstaller 单文件程序解包临时目录会失败，
  报 `Could not create temporary directory!`。此时可在 `Windows-Ruijie.spec` 中把
  `runtime_tmpdir=None` 改为一个可写目录，或用
  `pyinstaller --runtime-tmpdir "D:\some\writable\dir" Windows-Ruijie.spec` 重新打包。

## 局限性

- 学校登录接口若要求图形验证码（`validcode`），本程序无法完成认证；
- 抓包参数（`cookie`、`login_data`、`headers`）仍需按各自学校填写，参数写错可能被临时禁止登录；
- 开机自启与 Toast 通知为 Windows 实现；
- 请遵守学校网络使用规定，本工具仅用于减少手动重复登录的操作成本。

## 许可

沿用上游 [AGPL-3.0](LICENSE)。上游版权归 Red_lnn 所有，本迭代版同样以 AGPL-3.0 发布。
