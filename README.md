# Ruijie-ePorta-Tool（本地迭代版 v1.8.0）

基于 [Redlnn/Ruijie-ePorta-Tool](https://github.com/Redlnn/Ruijie-ePorta-Tool) v1.6 的**本地迭代分支**。

上游作者已毕业并停止维护。此分支把「跑一次就退出」的脚本重构成**带可视化界面的常驻守护工具**：
系统托盘、实时状态、掉线自动重连、可选保活、一键开机自启，并做了依赖与打包上的现代化改造。
原有的 v3 配置文件**无需任何修改**即可继续使用。

<h4>权责声明（沿用上游）</h4>

- 本程序仅供研究学习之用，无意对锐捷的认证机制做任何抵触性行为
- 本程序不可用于任何商业和不良用途，否则责任自负
- 本程序不保证在任何环境下能够通过，也不保证按时改进，编写及维护纯属个人爱好
- 本程序不保证经过严格测试对机器无害，由于未知的使用环境或不当的使用对机器造成的损害，责任由使用者全部承担

## 界面预览

运行后是一个原生 Windows 窗口（tkinter）：

- **顶部状态卡片**：彩色圆点 + 大字状态（已联网 / 未联网 / 未知），下面一行显示最近一次事件；
- **信息区**：认证服务器、账号、守护模式运行时长、当前探测地址；
- **按钮区**：立即登录、立即断网、刷新状态、开始/停止守护、开机自启（勾选框）、打开日志、打开配置文件；
- **运行日志**：带时间戳的滚动日志，按成功（绿）/ 失败（红）/ 警告（黄）着色；
- **底部**：当前日志文件路径。

关闭窗口不会退出程序，而是**最小化到系统托盘**；托盘图标右下角的圆点颜色表示联网状态
（绿=已联网、红=未联网、灰=未知），右键菜单可完成上面几乎所有操作，
左键单击则重新打开主界面。

> 「打开日志」「配置文件」按钮会优先调用系统默认程序；若该文件类型在本机没有关联程序
> （`.yml` 很常见），会自动改用记事本打开，再不行则在资源管理器中定位该文件，
> 并在日志区显示实际使用了哪种方式——不会出现"点了没反应"。

## 相比上游 v1.6 的变化

| 能力 | 上游 v1.6 | 本迭代版 v1.8 |
| --- | --- | --- |
| 可视化界面 | 无（仅弹窗） | **主窗口 + 系统托盘**，状态与日志可视 |
| 运行方式 | 执行一次后退出 | 界面常驻 / `--watch` 托盘后台 / `--once` 兼容旧行为 |
| 掉线恢复 | 需人工再次运行 | 自动检测并重连，失败按指数退避（默认 5s→…→300s 上限） |
| 保活 | 无 | `keepalive_interval` 定期探测，维持链路活跃 |
| 开机自启 | 手工放快捷方式到启动目录 | 界面勾选或 `--install-autostart` 一键完成 |
| 界面依赖 | tkinter（体积大且自启时会弹窗阻塞） | tkinter + pystray；守护模式不弹窗，托盘常驻 |
| 第三方依赖 | pywin32、tinyWinToast(git)、PyYAML | PyYAML、pystray、Pillow（无 git 依赖） |
| 依赖管理 | poetry，Python `>=3.9,<3.11` | 标准 `pyproject.toml` + `requirements.txt`，Python `>=3.9` |
| 出错处理 | 直接抛异常退出 | 非 JSON / HTTP 错误 / 超时均给出明确提示并记录日志 |
| 可测试性 | 无 | mock ePortal 服务器 + 95 项 pytest + 端到端自检脚本 |
| 日志 | 无 | `logs/ruijie-eporta.log`，自动轮转，日志中不含密码 |

## 快速开始

### 方式一：图形界面（推荐）

把 `Ruijie_ePorta_Tool.exe` 和你的 `config.yml` 放在**同一个目录**，双击运行：
界面打开并**自动开始守护**（可在界面上随时停止）。

### 方式二：托盘后台（开机自启使用的就是它）

```powershell
.\Ruijie_ePorta_Tool.exe --watch --quiet      # 无窗口，仅托盘图标 + 守护
.\Ruijie_ePorta_Tool.exe --install-autostart  # 装机自启（默认就是 --watch --quiet）
```

### 方式三：命令行

```powershell
.\Ruijie_ePorta_Tool.exe --once        # 旧版行为：检查校园网 → 已联网询问是否断网 → 未联网则登录
.\Ruijie_ePorta_Tool.exe --login       # 只登录一次
.\Ruijie_ePorta_Tool.exe --logout      # 只断网一次
.\Ruijie_ePorta_Tool.exe --status      # 输出状态 JSON（建议用 Console 版）
```

### 方式四：从源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m ruijie_eporta            # 打开界面
.\.venv\Scripts\python.exe -m ruijie_eporta --watch    # 托盘后台守护
```

## 命令行用法

```text
Ruijie_ePorta_Tool.exe [选项]
```

| 选项 | 说明 |
| --- | --- |
| 无参数 | **打开图形界面并自动开始守护** |
| `--gui` | 同上（显式指定） |
| `--watch` / `-w` | 守护模式：默认最小化到托盘常驻 |
| `--once` | 旧版单次流程：判断校园网 → 已联网询问是否断网 → 未联网则登录 |
| `--login` / `--logout` | 只执行一次登录 / 断网（无界面） |
| `--status` | 输出状态 JSON（无界面，Console 版可见） |
| `--install-autostart` / `--remove-autostart` | 安装 / 卸载开机自启 |
| `--no-watch` | 打开界面但不自动开始守护 |
| `--no-tray` | 不创建托盘图标（`--watch` 时为纯后台运行） |
| `--yes` / `--no` | 已联网时直接选择断网 / 不断网，不弹窗 |
| `--quiet` | 不发送系统通知，只写日志 |
| `--no-toast` / `--dialog` | 关闭 Windows 通知 / 额外弹出消息框 |
| `--config PATH` | 指定配置文件（也可用环境变量 `RUJIE_EPORTA_CONFIG`） |
| `--log-file PATH` | 指定日志文件 |
| `--verbose` / `-v` | 输出调试日志 |
| `--allow-multiple` | 允许同时运行多个实例（默认单实例） |
| `--iterations N` | 守护模式最多循环 N 次后退出（调试用） |
| `--version` | 显示版本 |

> 程序默认单实例运行。若已有实例（例如开机自启的托盘守护）在运行，
> 再次双击会提示「程序已在运行」，此时请**在系统托盘里打开主界面**。

## 免抓包模式（推荐）

不想抓包？把 `config.yml` 里的 `auto` 段填两行即可：

```yaml
auto:
  enabled: true
  user_id: '你的学号'
  password: '你的密码'
  service: '中国电信' # 留空则使用学校默认运营商
```

启用后 `url` / `cookie` / `login_data.queryString` / `headers.Referer` **都可以留空**。

程序会自动完成原来需要抓包才能拿到的步骤：

1. 访问探测地址时**不跟随重定向**，从网关返回的 `Location` 中取得认证页地址，以及
   `wlanuserip`、`mac`、`nasid`、`vid` 等参数；
2. 打开认证页，建立 `JSESSIONID` 会话；
3. 调用 `InterFace.do?method=pageInfo` 取得 RSA 公钥与运营商列表；
4. 按认证页 `security.js` 的算法加密密码：明文为 `密码>mac`，**反转**后做**原生 RSA**
   （无 PKCS#1 填充），输出十六进制；
5. 组装登录表单（`queryString` 为原始查询串的单次 URL 编码）并提交。

> ⚠️ `auto.password` 是**明文**保存的，请勿把填好密码的 `config.yml` 发给别人。
> 想确认生效情况，可看界面日志或运行 `--status`，日志里会打印实际使用的服务器与运营商。

### 自动配置：把参数固化到配置文件（`--auto-setup`）

想让探测到的参数落到 `config.yml` 里（便于查看、备份，或发给自己另一台电脑），可以执行：

```powershell
Ruijie_ePorta_Tool.exe --auto-setup
```

也可以在界面上点「**自动获取参数**」按钮。它会访问学校认证页，把认证服务器、cookie、
**加密后的密码密文**、`queryString`、`Referer` 写入配置文件（原文件自动备份为
`config.yml.bak-<时间>`）。写入内容等价于一次抓包结果，所以**即使把 `auto.enabled` 关掉，
`--login` 也能直接登录**。

> 首次运行时，若 `auto.enabled: true` 且还没有抓包参数，程序会自动完成这一步。
> 这些参数与本机 IP/MAC 绑定，换网络环境后重新执行一次即可。

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

也可以在界面里直接勾选/取消「开机自启」。

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

常用参数：`-Clean`（先清理）、`-SkipTests`、`-SkipInstall`。也可以直接双击 `build.bat`。

## 测试

所有测试都是**离线**的：`tests/mock_eportal.py` 会启动一个本地模拟 ePortal 服务器
（同时提供 `generate_204` 探测端点），验证登录/断网、探测判定、重连状态机与界面逻辑。

```powershell
# 单元测试（配置 / 认证 / 探测 / 重连 / 界面控制器 / 托盘 / 界面冒烟）
.\.venv\Scripts\python.exe -m pytest -q

# 端到端自检（真实 CLI 对接本地模拟服务器，含掉线自动重连场景）
.\.venv\Scripts\python.exe tools\e2e_check.py

# 也可以直接验证打包产物（需用 Console 版才能看到 --status 输出）
.\.venv\Scripts\python.exe tools\e2e_check.py --exe .\dist\Ruijie_ePorta_Tool_Console.exe
```

## 已知问题

- 少数受限环境（例如带应用白名单的终端管控软件）下，PyInstaller 单文件程序解包临时目录会失败，
  报 `Could not create temporary directory!`。此时可在 `Windows-Ruijie.spec` 中把
  `runtime_tmpdir=None` 改为一个可写目录，或
  `pyinstaller --runtime-tmpdir "D:\some\writable\dir" Windows-Ruijie.spec` 重新打包。
- 无桌面会话（例如以服务身份运行计划任务）时无法创建窗口，程序会自动回退为命令行模式。

## 局限性

- 学校登录接口若要求图形验证码（`validcode`），本程序无法完成认证；
- 抓包参数（`cookie`、`login_data`、`headers`）仍需按各自学校填写，参数写错可能被临时禁止登录；
- 开机自启与系统托盘、Toast 通知均为 Windows 实现；
- 请遵守学校网络使用规定，本工具仅用于减少手动重复登录的操作成本。

## 许可

沿用上游 [AGPL-3.0](LICENSE)。上游版权归 Red_lnn 所有，本迭代版同样以 AGPL-3.0 发布。
