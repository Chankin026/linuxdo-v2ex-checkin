# linuxdo-v2ex-checkin

一个用于自动执行论坛日常任务的 Python 项目，当前只保留并支持这些能力：

- LinuxDo 登录校验、浏览任务、Connect 信息采集
- V2EX 每日签到
- 小黑盒每日签到
- NodeSeek 每日签到
- Telegram 通知
- VPS `systemd` 定时运行
- VPS 每次执行前自动拉取最新代码并按需更新依赖

建议仅用于个人账号的自动化辅助，不建议滥用、多线程批量刷站或批量养号。

## 项目结构

当前仓库只保留与运行能力直接相关的文件：

- `main.py`：统一入口，按配置顺序执行 LinuxDo、V2EX、NodeSeek、小黑盒
- `v2ex.py`：V2EX Cookie 签到
- `nodeseek.py`：NodeSeek Cookie / 账号密码签到
- `captcha_solver.py`：NodeSeek 账号密码模式下的 YesCaptcha 支持
- `xiaoheihe.py`：小黑盒纯 Cookie signer 签到
- `pure_signin.py`：小黑盒独立调试脚本
- `notify.py`：Telegram / Gotify / ServerChan3 / wxpush 通知
- `deploy/vps/*`：VPS 安装、`systemd`、执行前自动更新

## 支持能力

### LinuxDo

- 支持 `Cookie` 登录
- 支持账号密码登录
- 支持 `Cookie -> 账号密码` 自动回退
- 登录后可自动浏览主题、随机滚动、尝试点赞
- 会抓取 `https://connect.linux.do/` 的汇总信息并写入通知
- 在 VPS 上支持把刷新后的 `LINUXDO_COOKIES` 自动回写到 env 文件

### V2EX

- 使用 `Cookie` 完成 `/mission/daily` 签到
- 成功通知中会带上：
  - 今日签到获得多少铜币
  - 当前余额多少铜币

### 小黑盒

- 只保留 `Cookie + Python 本地 signer` 模式
- `main.py` 会在配置了 `XIAOHEIHE_COOKIE` 时自动执行签到
- 默认只需要一条完整 Cookie
- 支持查询签到状态、执行签到、失败重试和结果通知
- 不再依赖 Java、Maven、APK、`libailab.so`、`vendor/` 或 UI 自动化环境

### NodeSeek

- 支持 `Cookie` 登录
- 支持账号密码登录
- 支持 `Cookie -> 账号密码` 自动回退
- 账号密码登录时支持 `YesCaptcha` 处理 Turnstile 验证
- 支持多账号顺序执行
- 成功通知中会带上：
  - 今日签到获得多少鸡腿
  - 当前总鸡腿
  - 连续签到天数
- 在 VPS 上支持把刷新后的 Cookie 自动回写到对应 env 变量

## 快速开始

### 本地运行

```bash
git clone <your-repo-url>
cd linuxdo-v2ex-checkin
python -m venv .venv
.venv/bin/pip install -r requirements.txt
python main.py
```

Windows 下请自行替换为对应的虚拟环境命令。

### VPS 运行

完整部署说明请看：

- [deploy/vps/README.md](deploy/vps/README.md)
- [deploy/vps/linuxdo-v2ex-checkin.env.example](deploy/vps/linuxdo-v2ex-checkin.env.example)

## 环境变量

### LinuxDo

| 变量名 | 用途 | 说明 |
| --- | --- | --- |
| `LINUXDO_COOKIES` | LinuxDo Cookie 字符串 | 推荐配置 |
| `LINUXDO_USERNAME` | LinuxDo 用户名或邮箱 | VPS / 本地可用 |
| `LINUXDO_PASSWORD` | LinuxDo 密码 | VPS / 本地可用 |
| `BROWSE_ENABLED` | 是否执行浏览任务 | 默认 `true` |
| `LINUXDO_ENV_FILE` | 本地 env 文件路径 | 默认 `/etc/linuxdo-v2ex-checkin.env` |
| `LINUXDO_SOLVER_TYPE` | 验证码方案 | 当前使用 `yescaptcha` |
| `CLIENTT_KEY` | 通用 YesCaptcha key | LinuxDo / NodeSeek 账号密码登录共用 |
| `LINUXDO_YESCAPTCHA_API_BASE_URL` | YesCaptcha 接口地址 | 默认 `https://api.yescaptcha.com` |
| `LINUXDO_YESCAPTCHA_ADVANCED` | YesCaptcha 高级模式 | 可选，默认关闭 |
| `LINUXDO_YESCAPTCHA_HCAPTCHA_MAX_RETRIES` | LinuxDo hCaptcha 最大轮询次数 | 可选，默认 `45` |
| `LINUXDO_YESCAPTCHA_HCAPTCHA_RETRY_INTERVAL` | LinuxDo hCaptcha 轮询间隔（秒） | 可选，默认 `4` |
| `LINUXDO_YESCAPTCHA_HCAPTCHA_TIMEOUT` | LinuxDo hCaptcha 单次请求超时（秒） | 可选，默认 `600` |

说明：

- 如果同时配置了 `LINUXDO_COOKIES` 和账号密码，会优先使用 Cookie
- VPS / 本地下，Cookie 失效后会尝试账号密码登录
- LinuxDo 的 hCaptcha 默认会按 `45` 次重试、每次间隔 `4` 秒、单次请求超时 `600` 秒执行，不配置也会生效
- LinuxDo 浏览器固定使用有头模式；在 VPS 上如果 `DISPLAY` 为空，`deploy/vps/run.sh` 会自动使用 `xvfb-run`

### V2EX

| 变量名 | 用途 | 说明 |
| --- | --- | --- |
| `V2EX_ENABLED` | 是否启用 V2EX 任务 | 默认会根据 Cookie 自动判断 |
| `V2EX_COOKIE` | 完整 Cookie 字符串 | 优先级高于 `V2EX_A2` |
| `V2EX_A2` | 只提供 `A2` Cookie | 更简洁的写法 |

### 小黑盒

| 变量名 | 用途 | 说明 |
| --- | --- | --- |
| `XIAOHEIHE_ENABLED` | 是否启用小黑盒任务 | 默认会根据 `XIAOHEIHE_COOKIE` 自动判断 |
| `XIAOHEIHE_ACCOUNT_NAME` | 通知中的账号名 | 可选 |
| `XIAOHEIHE_COOKIE` | 完整 Cookie 字符串 | 必填，至少包含 `pkey` 与 `x_xhh_tokenid` |
| `XIAOHEIHE_HEADERS_JSON` | 额外请求头 | JSON 对象字符串，作用于 state/sign 请求 |
| `XIAOHEIHE_TIMEOUT` | 实际 HTTP 请求超时 | 默认 `20` 秒 |
| `XIAOHEIHE_RETRY_TIMES` | 请求重试次数 | 默认 `6` |
| `XIAOHEIHE_RETRY_MIN_DELAY` | 重试最小等待秒数 | 默认 `3` |
| `XIAOHEIHE_RETRY_MAX_DELAY` | 重试最大等待秒数 | 默认 `12` |
| `XIAOHEIHE_IMPERSONATE` | 请求指纹 | 默认继承 `IMPERSONATE_VERSION` |
| `XIAOHEIHE_HEYBOX_ID` | 手动指定账号 ID | 仅在无法从 `pkey` 自动解析时需要 |
| `XIAOHEIHE_ANDROID_ID` | 签名链使用的 Android ID | 默认内置 |
| `XIAOHEIHE_DEVICE_MODEL` | 签名链使用的设备型号 | 默认 `SM-S9210` |

说明：

- `main.py` 会在配置了 `XIAOHEIHE_COOKIE` 时自动执行小黑盒签到
- 当前只保留纯 Cookie signer 模式
- 如果设置了旧的 `XIAOHEIHE_REQUEST_MODE`，程序会忽略旧值并自动回退到 signer

### NodeSeek

| 变量名 | 用途 | 说明 |
| --- | --- | --- |
| `NODESEEK_ENABLED` | 是否启用 NodeSeek 任务 | 默认会根据账号配置自动判断 |
| `NODESEEK_SOLVER_TYPE` | 验证码方案 | 使用 `yescaptcha` |
| `CLIENTT_KEY` | 通用 YesCaptcha key | LinuxDo / NodeSeek 账号密码登录共用 |
| `NODESEEK_YESCAPTCHA_API_BASE_URL` | YesCaptcha 接口地址 | 默认 `https://api.yescaptcha.com` |
| `NODESEEK_YESCAPTCHA_ADVANCED` | YesCaptcha 高级模式 | 可选 |
| `NODESEEK_RANDOM` | 签到接口是否附带随机参数 | 默认 `true` |
| `NODESEEK_IMPERSONATE` | 请求指纹 | 默认 `chrome136` |

单账号配置：

| 变量名 | 用途 | 说明 |
| --- | --- | --- |
| `NODESEEK_NAME` | 通知中的账号名 | 可选 |
| `NODESEEK_COOKIE` | NodeSeek Cookie | 可单独使用 |
| `NODESEEK_USERNAME` | NodeSeek 用户名 | 可和密码配对使用 |
| `NODESEEK_PASSWORD` | NodeSeek 密码 | 可和用户名配对使用 |
| `NODESEEK_EMAIL_IMAP_PASSWORD` | 邮箱 IMAP 授权码 | 触发邮箱验证时才需要，建议使用授权码 |

邮箱验证的可选覆盖项：

| 变量名 | 用途 | 说明 |
| --- | --- | --- |
| `NODESEEK_EMAIL` | NodeSeek 绑定邮箱 | 默认从登录返回的邮箱验证地址自动读取 |
| `NODESEEK_EMAIL_IMAP_HOST` | 邮箱 IMAP 服务器 | 默认按邮箱域名推断，例如 `imap.qq.com` |
| `NODESEEK_EMAIL_IMAP_PORT` | 邮箱 IMAP SSL 端口 | 默认 `993` |
| `NODESEEK_EMAIL_IMAP_USERNAME` | 邮箱登录账号 | 默认等于 `NODESEEK_EMAIL` 或登录返回的邮箱 |
| `NODESEEK_EMAIL_IMAP_MAILBOX` | 邮箱目录 | 默认 `INBOX` |
| `NODESEEK_EMAIL_CODE_TIMEOUT` | 等待验证码秒数 | 默认 `300` |
| `NODESEEK_EMAIL_CODE_POLL_INTERVAL` | 轮询邮箱间隔秒数 | 默认 `10` |

多账号配置使用编号变量，例如：

```env
NODESEEK_NAME_1=main
NODESEEK_COOKIE_1=nodepay_session=account1_cookie
NODESEEK_USERNAME_1=account1_username
NODESEEK_PASSWORD_1=account1_password
NODESEEK_EMAIL_IMAP_PASSWORD_1=account1_imap_app_password

NODESEEK_NAME_2=backup
NODESEEK_COOKIE_2=nodepay_session=account2_cookie
NODESEEK_USERNAME_2=account2_username
NODESEEK_PASSWORD_2=account2_password
```

### 通知

| 变量名 | 用途 | 说明 |
| --- | --- | --- |
| `NOTIFY_TIMEZONE` | 通知时间时区 | 默认 `Asia/Shanghai` |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | Telegram 通知 |
| `TELEGRAM_CHAT_ID` | Telegram Chat ID | Telegram 通知 |
| `GOTIFY_URL` | Gotify 服务地址 | Gotify 通知 |
| `GOTIFY_TOKEN` | Gotify Token | Gotify 通知 |
| `SC3_PUSH_KEY` | ServerChan3 SendKey | ServerChan3 通知 |
| `WXPUSH_URL` | wxpush 服务地址 | wxpush 通知 |
| `WXPUSH_TOKEN` | wxpush Token | wxpush 通知 |

### VPS 自动更新

| 变量名 | 用途 | 说明 |
| --- | --- | --- |
| `AUTO_UPDATE` | VPS 每次执行前是否拉取最新代码 | 默认 `true` |
| `AUTO_INSTALL_DEPS` | 依赖变化时是否自动 `pip install -r requirements.txt` | 默认 `true` |
| `AUTO_UPDATE_STRICT` | 更新失败时是否中止本次任务 | 默认 `true` |

说明：

- 当远端存在新提交、且 `deploy/vps/run.sh` 准备执行 fast-forward 更新时，会先检测仓库是否有本地改动
- 如果存在已跟踪或未跟踪改动，会先执行 `git stash push --include-untracked`
- 原改动会保留在 `git stash list` 中，便于后续人工检查

## 配置示例

### VPS 常用组合

```env
LINUXDO_COOKIES=_t=xxx; _forum_session=yyy; cf_clearance=zzz
LINUXDO_USERNAME=your_username_or_email
LINUXDO_PASSWORD=your_password

V2EX_ENABLED=true
V2EX_A2=your_v2ex_a2

XIAOHEIHE_ENABLED=true
XIAOHEIHE_COOKIE=pkey=your_pkey; x_xhh_tokenid=your_token

NODESEEK_ENABLED=true
NODESEEK_SOLVER_TYPE=yescaptcha
CLIENTT_KEY=your_yescaptcha_key
NODESEEK_NAME=main
NODESEEK_COOKIE=nodepay_session=xxx
NODESEEK_USERNAME=your_nodeseek_username
NODESEEK_PASSWORD=your_nodeseek_password
NODESEEK_EMAIL_IMAP_PASSWORD=your_imap_app_password

TELEGRAM_BOT_TOKEN=123456:ABCDEF
TELEGRAM_CHAT_ID=123456789
NOTIFY_TIMEZONE=Asia/Shanghai
```

### 单独调试小黑盒

```bash
XIAOHEIHE_COOKIE='pkey=...; x_xhh_tokenid=...' python xiaoheihe.py
```

或使用独立调试脚本：

```bash
python pure_signin.py "pkey=...; x_xhh_tokenid=..."
```

## 实际行为

### LinuxDo

1. 优先使用 `LINUXDO_COOKIES`
2. 如果 Cookie 失效，且配置了账号密码，则尝试账号密码登录
3. 登录成功后校验账号页登录状态
4. 默认执行浏览任务；如果显式设置 `BROWSE_ENABLED=false`，则跳过浏览任务
5. 读取 Connect 信息
6. 发送通知

### V2EX

1. 打开 `/mission/daily`
2. 判断今天是否已经签到
3. 如未签到则执行领取
4. 读取 `/balance`
5. 发送通知

### 小黑盒

1. 从 `XIAOHEIHE_COOKIE` 中提取 `pkey`、`x_xhh_tokenid` 和 `heybox_id`
2. 在 Python 本地生成 `get_sign_state` 请求签名
3. 如果今天已签到，则直接发送成功通知
4. 如果还未签到，再生成 `sign` 请求
5. 对 `429 / 5xx` 自动重试
6. 成功后再次校验签到状态并发送通知

### NodeSeek

1. 对每个账号按顺序执行
2. 优先尝试 Cookie
3. Cookie 无效时，使用账号密码 + YesCaptcha 登录
4. 登录成功后签到
5. 读取 credit 历史统计今日鸡腿和当前总鸡腿
6. 发送通知

## FAQ

### 为什么 Telegram 发不出去

通常是下面几种原因：

- `TELEGRAM_BOT_TOKEN` 写错
- `TELEGRAM_CHAT_ID` 写成了 Bot Token
- 你还没有先给 Bot 发过消息
- 群组没有把 Bot 拉进去

### 为什么 NodeSeek 还保留 `captcha_solver.py`

因为当前仓库仍保留 NodeSeek 的账号密码回退能力。只要你希望在 Cookie 失效后仍能自动登录，就需要 YesCaptcha 支持。

## 依赖

当前 Python 依赖：

```text
DrissionPage==4.1.0.18
tabulate==0.9.0
loguru==0.7.2
curl-cffi
bs4
```

## 部署

如果你只想快速在 VPS 上部署，请直接看：

- [deploy/vps/README.md](deploy/vps/README.md)
- [deploy/vps/linuxdo-v2ex-checkin.env.example](deploy/vps/linuxdo-v2ex-checkin.env.example)
