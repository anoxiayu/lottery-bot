
# 🎲 大乐透小助手 (Lottery Bot)

[![Python](https://img.shields.io/badge/Python-3.9-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.3-green.svg)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-Ready-blue.svg)](https://www.docker.com/)

基于 Python Flask 构建的大乐透自动兑奖与推送系统。专为 NAS（威联通/群晖）及 Docker 环境设计，支持多用户管理、多期连买自动核对、历史中奖查询以及 Server酱微信推送。

---

## ✨ 主要功能

*   **📊 自动兑奖**：自动同步官方开奖数据，计算中奖等级与金额。
*   **🎫 多期管理**：支持设置“开始期号”与“连买期数”，自动计算有效期。
*   **📲 微信推送**：集成 [Server酱](https://sct.ftqq.com/)，开奖日自动推送到微信（仅在有效期内且中奖/开奖时推送）。
*   **🕒 自定义任务**：支持在网页端自定义每日自动检查的时间（默认周一/三/六 22:00）。
*   **📜 历史回溯**：支持查询某注号码在过去 50 期内的中奖情况，并生成汇总报告推送。
*   **👀 可视化规则**：内置大乐透中奖规则图解，直观易懂。
*   **🔐 多用户隔离**：支持多用户注册登录，数据与推送 Key 相互隔离。
*   **🎨 精美 UI**：采用 3D 拟态风格圆球设计，移动端完美适配。

---

## 📂 目录结构

在部署前，请确保您的文件结构如下：

```text
lottery_bot/
├── Dockerfile           # 构建文件
├── requirements.txt     # 依赖列表
├── app.py               # 主程序
├── data/                # [自动生成] 存放数据库文件，需映射到宿主机
└── templates/           # 前端页面
    ├── index.html
    ├── login.html
    ├── register.html
    ├── history.html
    └── rules.html
```

---

## 🚀 Docker 部署指南 (推荐)

### 1. 准备环境
将项目文件上传至 NAS 或服务器的文件夹，例如 `/share/Container/lottery_bot`。

### 2. 构建镜像
进入项目目录并运行构建命令：

```bash
cd /share/Container/lottery_bot
docker build -t my-lotto-v7 .
```

### 3. 运行容器
**注意**：必须挂载 `data` 目录，否则重启容器后账号和彩票数据会丢失。

```bash
docker run -d \
  --name lotto-web \
  -p 5000:5000 \
  -v /share/Container/lottery_bot/data:/app/data \
  --restart unless-stopped \
  my-lotto-v7
```

*   `-p 5000:5000`: 将容器的 5000 端口映射到主机的 5000 端口。
*   `-v ...:/app/data`: 数据持久化挂载。
*   `--restart unless-stopped`: 保证 NAS 重启后服务自动启动。

### 4. 访问服务
打开浏览器访问 `http://<NAS_IP>:5000`。

---

## 📖 使用说明

1.  **注册/登录**：首次使用请点击“去注册”创建一个账号。
2.  **配置推送**：
    *   登录后，在主页底部的“系统设置”卡片中，填入您的 **Server酱 SendKey**。
    *   设置自动推送时间（建议设置为 `21:40` 或 `22:00`，确保官方已开奖）。
    *   点击“保存设置”。
3.  **添加号码**：
    *   点击右上角的 **`+ 添加`**。
    *   输入前区和后区号码（支持输入2位数字自动跳转）。
    *   **开始期号**会自动填入下一期，您可以修改。
    *   **连买期数**默认为1，最大支持30期，系统会自动计算结束期号。
4.  **查看结果**：
    *   **待开奖**：蓝色标签。
    *   **已中奖**：黄色背景，显示奖金。
    *   **已过期**：灰色背景。
5.  **往期查询**：
    *   点击号码卡片上的 **`🧾`** 图标，可查看该号码在有效期内的所有历史中奖记录。

---

## ⚙️ 环境变量 (可选)

虽然推荐在网页端配置，但依然支持通过 Docker 环境变量预设 Server酱 Key（仅针对首次生成的数据库配置有效）：

*   `SCKEY`: Server酱的 SendKey。

---

## 🛠️ 技术栈

*   **Backend**: Python 3.9, Flask
*   **Database**: SQLite, SQLAlchemy
*   **Task Queue**: APScheduler (BackgroundScheduler)
*   **Frontend**: HTML5, Bootstrap 5, CSS3 (3D Neumorphism)
*   **API**: China Sports Lottery Official Gateway

---

## ⚠️ 免责声明

1.  本项目仅供**技术学习和个人娱乐**使用。
2.  所有开奖数据来源于体彩官方公开接口，但在极少数网络波动情况下可能存在延迟。
3.  **中奖结果请以中国体育彩票官方实体票面和官方公告为准。**
4.  作者不对因使用本软件造成的任何损失负责。请理性购彩。

---

## 📝 更新日志

*   **v7.1**: 修复 Docker 部署兼容性，优化规则页面入口，UI 细节打磨。
*   **v7.0**: 新增可视化中奖规则页面，优化主页头部设计。
*   **v6.0**: 增加网页端自定义推送时间设置，蓝球输入框居中修复。
*   **v5.0**: 支持多期连买历史查询，支持历史汇总推送。
*   **v4.0**: 引入多期（开始期号-结束期号）逻辑。
*   **v3.0**: 引入多用户登录系统。

