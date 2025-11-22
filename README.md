# 🎲 大乐透小助手 (Lottery Bot)

[![Python](https://img.shields.io/badge/Python-3.9-blue.svg)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.3-green.svg)](https://flask.palletsprojects.com/)
[![Docker](https://img.shields.io/badge/Docker-anoxiayu%2Flottery--bot-blue)](https://hub.docker.com/r/anoxiayu/lottery-bot)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

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

## 🐳 Docker 快速部署 (推荐)

本镜像已发布至 Docker Hub，无需下载源码，直接拉取即可运行。

### 1. 命令行部署 (SSH)

适用于威联通、群晖或 Linux 服务器。请确保您已创建好数据存放目录（例如 `/share/Container/lottery_bot/data`）。

```bash
# 1. 拉取最新镜像
docker pull anoxiayu/lottery-bot:latest

# 2. 运行容器 (请根据实际情况修改 -v 挂载路径)
docker run -d \
  --name lotto-web \
  -p 5000:5000 \
  -v /share/Container/lottery_bot/data:/app/data \
  --restart unless-stopped \
  anoxiayu/lottery-bot:latest
```

*   **`-p 5000:5000`**: 访问端口为 5000。
*   **`-v ...:/app/data`**: **[重要]** 数据持久化目录。必须挂载，否则重启后账号数据会丢失。
*   **`--restart unless-stopped`**: 开机自启。

### 2. 威联通 Container Station 部署

1.  打开 **Container Station**。
2.  点击 **Images (镜像)** -> **Pull (拉取)**。
3.  搜索/输入镜像名：`anoxiayu/lottery-bot`，版本选择 `latest`。
4.  拉取完成后点击 **Create (创建)**。
5.  在 **Advanced Settings (高级设置)** 中：
    *   **Network**: 端口映射 `5000` (主机) -> `5000` (容器)。
    *   **Shared Folders**: 挂载本机文件夹到 `/app/data`。

---

## 🛠️ 手动构建 (可选)

如果您需要修改源码或进行二次开发，可以手动构建。

1.  **下载源码**：
    ```text
    lottery_bot/
    ├── Dockerfile
    ├── requirements.txt
    ├── app.py
    └── templates/
    ```
2.  **构建镜像**：
    ```bash
    cd lottery_bot
    docker build -t my-lotto-local .
    ```
3.  **运行**：
    ```bash
    docker run -d -p 5000:5000 -v $(pwd)/data:/app/data my-lotto-local
    ```

---

## 📖 使用说明

1.  **访问**：打开浏览器访问 `http://<NAS_IP>:5000`。
2.  **注册**：首次使用请点击“去注册”创建一个账号。
3.  **配置**：
    *   登录后，在主页底部填入 **Server酱 SendKey**。
    *   设置自动推送时间（建议 `21:40` 或 `22:00`）。
4.  **添加号码**：
    *   点击 **`+ 添加`**，输入号码。
    *   **连买期数**默认为1，最大30期，系统自动计算有效期。
5.  **状态说明**：
    *   🟦 **待开奖**：期号未到。
    *   🟨 **已中奖**：中奖显示金额。
    *   ⬜ **已过期**：期号已过。

---

## ⚖️ 开源协议 (License)

本项目采用 **MIT 许可证** 开源。

这意味着您可以自由地使用、复制、修改、合并、出版发行、散布、再授权及贩售本软件的副本，但您必须在您的衍生作品中保留本项目的版权声明和许可声明。

[查看完整的 MIT 许可证文件](https://opensource.org/licenses/MIT)

---

## ⚠️ 免责声明 (Disclaimer)

在使用本软件之前，请仔细阅读以下条款：

1.  **非官方应用**：本软件为个人开发者开源项目，与中国体育彩票中心无任何关联。
2.  **数据准确性**：开奖数据来自网络接口，**所有中奖结果请以实体彩票和官方公告为准**。作者不保证数据的绝对实时性与准确性。
3.  **非购彩平台**：本软件仅为本地数据记录与通知工具，**不具备**任何在线购买、资金交易功能。
4.  **免责条款**：作者不对因使用本软件导致的任何直接或间接损失（如奖金误判、漏买、误买、Key泄露等）承担责任。
5.  **理性购彩**：彩票由于其随机性，请理性对待，**禁止沉迷**。

---

## 🛠️ 技术栈

*   **Backend**: Python 3.9, Flask
*   **Database**: SQLite, SQLAlchemy
*   **Frontend**: HTML5, Bootstrap 5, CSS3 (Neumorphism Design)
*   **Deployment**: Docker, Docker Hub

