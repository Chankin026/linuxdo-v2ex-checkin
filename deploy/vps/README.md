# VPS Deployment

这是当前仓库的最简稳定 VPS 方案：

1. 克隆仓库到 `/opt/linuxdo-v2ex-checkin`
2. 创建 Python 虚拟环境
3. 安装 Google Chrome
4. 把配置写入 `/etc/linuxdo-v2ex-checkin.env`
5. 启用 `systemd timer`

推荐系统：Ubuntu 22.04 / 24.04 x86_64

## 最快路径

```bash
curl -O https://raw.githubusercontent.com/<your-user>/<your-repo>/<your-branch>/deploy/vps/install.sh
sudo bash install.sh <your-repo-url>
```

或者在你已经克隆仓库后执行：

```bash
cd /opt/linuxdo-v2ex-checkin
sudo bash deploy/vps/install.sh <your-repo-url>
```

## 1. 服务器准备

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip wget unzip xvfb fonts-liberation libasound2 libatk-bridge2.0-0 libatk1.0-0 libatspi2.0-0 libc6 libcairo2 libcups2 libdbus-1-3 libdrm2 libgbm1 libglib2.0-0 libgtk-3-0 libnspr4 libnss3 libpango-1.0-0 libu2f-udev libvulkan1 libx11-6 libx11-xcb1 libxcb1 libxcomposite1 libxdamage1 libxext6 libxfixes3 libxkbcommon0 libxrandr2 xdg-utils
cd /opt
sudo git clone <your-repo-url> linuxdo-v2ex-checkin
cd /opt/linuxdo-v2ex-checkin
sudo python3 -m venv .venv
sudo .venv/bin/pip install -U pip
sudo .venv/bin/pip install -r requirements.txt
```

## 2. 安装 Chrome

```bash
cd /tmp
wget -O google-chrome-stable_current_amd64.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt install -y ./google-chrome-stable_current_amd64.deb
google-chrome --version
```

## 3. 配置 `/etc/linuxdo-v2ex-checkin.env`

```bash
sudo cp /opt/linuxdo-v2ex-checkin/deploy/vps/linuxdo-v2ex-checkin.env.example /etc/linuxdo-v2ex-checkin.env
sudo nano /etc/linuxdo-v2ex-checkin.env
```

### LinuxDo

推荐 VPS 使用混合模式：

```bash
LINUXDO_COOKIES=_t=xxx; _forum_session=yyy; cf_clearance=zzz
LINUXDO_USERNAME=your_username_or_email
LINUXDO_PASSWORD=your_password
```

如果没有 `cf_clearance`，可以直接省略。

这样程序会：

1. 先尝试 Cookie
2. Cookie 失效后回退到账号密码
3. 自动把刷新后的 `LINUXDO_COOKIES` 回写到 `/etc/linuxdo-v2ex-checkin.env`

如果你需要账号密码登录并处理验证码，再补：

```bash
CLIENTT_KEY=your_yescaptcha_client_key
```

### V2EX

V2EX 只使用 Cookie：

```bash
V2EX_ENABLED=true
V2EX_A2=your_a2_cookie
```

或者直接使用完整 Cookie：

```bash
V2EX_COOKIE=A2=your_a2_cookie; PB3_SESSION=optional_if_present
```

### NodeSeek

共享配置：

```bash
NODESEEK_ENABLED=true
NODESEEK_SOLVER_TYPE=yescaptcha
CLIENTT_KEY=your_yescaptcha_client_key
NODESEEK_RANDOM=true
NODESEEK_IMPERSONATE=chrome136
```

单账号混合模式：

```bash
NODESEEK_NAME=main
NODESEEK_COOKIE=nodepay_session=xxx
NODESEEK_USERNAME=your_username
NODESEEK_PASSWORD=your_password
```

NodeSeek 会：

1. 先尝试 Cookie
2. Cookie 无效时，使用账号密码 + YesCaptcha 登录
3. 把刷新后的 Cookie 回写到 `/etc/linuxdo-v2ex-checkin.env`

双账号示例：

```bash
NODESEEK_ENABLED=true
NODESEEK_SOLVER_TYPE=yescaptcha
CLIENTT_KEY=your_yescaptcha_client_key

NODESEEK_NAME_1=main
NODESEEK_COOKIE_1=nodepay_session=account1_cookie
NODESEEK_USERNAME_1=account1_username
NODESEEK_PASSWORD_1=account1_password

NODESEEK_NAME_2=backup
NODESEEK_COOKIE_2=nodepay_session=account2_cookie
NODESEEK_USERNAME_2=account2_username
NODESEEK_PASSWORD_2=account2_password
```

### 小黑盒

小黑盒当前只保留纯 Cookie signer 模式。

最小配置：

```bash
XIAOHEIHE_ENABLED=true
XIAOHEIHE_COOKIE=pkey=your_pkey; x_xhh_tokenid=your_token
```

如果 `pkey` 无法自动解析出 `heybox_id`，再补：

```bash
XIAOHEIHE_HEYBOX_ID=your_heybox_id
```

程序会通过 `main.py` 自动调用 `xiaoheihe.py` 的本地 signer 签到链路，不需要任何额外的 Java、APK、`vendor/` 或 Android 自动化环境。

### Telegram 通知

```bash
NOTIFY_TIMEZONE=Asia/Shanghai
TELEGRAM_BOT_TOKEN=123456:ABCDEF
TELEGRAM_CHAT_ID=123456789
```

当前通知内容包括：

1. LinuxDo：登录结果、浏览摘要、Connect 信息
2. V2EX：今日奖励、当前余额
3. NodeSeek：今日奖励、当前余额、连续签到天数
4. 小黑盒：签到结果与账号信息

## 4. 安装 service 和 timer

```bash
sudo cp /opt/linuxdo-v2ex-checkin/deploy/vps/linuxdo-v2ex-checkin.service /etc/systemd/system/
sudo cp /opt/linuxdo-v2ex-checkin/deploy/vps/linuxdo-v2ex-checkin.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now linuxdo-v2ex-checkin.timer
```

## 5. 手动测试一次

```bash
sudo systemctl start linuxdo-v2ex-checkin.service
sudo systemctl status linuxdo-v2ex-checkin.service --no-pager
sudo journalctl -u linuxdo-v2ex-checkin.service -n 100 --no-pager
sudo systemctl list-timers --all | grep linuxdo-v2ex-checkin
```

## 每次执行前自动更新

VPS 服务会通过 `deploy/vps/run.sh` 启动。
无论是 `systemctl start linuxdo-v2ex-checkin.service` 还是定时任务触发，都会先执行更新阶段，再执行 `main.py`。

默认流程：

1. 记录当前本地提交
2. 执行 `git fetch --prune`
3. 如果远端有新代码，则执行 fast-forward 更新
4. 检查 `requirements.txt` 是否变化
5. 只有在依赖变化时才执行 `.venv/bin/pip install -r requirements.txt`
6. 最后执行 `main.py`

默认 `AUTO_UPDATE_STRICT=true`：

- 如果远端有新代码但更新失败，本次任务会直接中止，并发送通知
- 如果你更希望“即使更新失败也继续跑本地旧代码”，可以设置 `AUTO_UPDATE_STRICT=false`

可选环境变量：

```bash
AUTO_UPDATE=true
AUTO_INSTALL_DEPS=true
AUTO_UPDATE_STRICT=true
```

如果 `DISPLAY` 为空，`deploy/vps/run.sh` 会自动通过 `xvfb-run` 启动 `main.py`，因此 LinuxDo 的有头浏览器仍可正常运行。

## 后续更新

```bash
cd /opt/linuxdo-v2ex-checkin
sudo git pull
sudo .venv/bin/pip install -r requirements.txt
sudo systemctl start linuxdo-v2ex-checkin.service
```
