
---

# 🎲 大乐透管家 (Lottery Bot) v10

[![Python](https://img.shields.io/badge/Python-3.11-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-2.3+-green.svg?style=flat-square&logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Docker Image](https://img.shields.io/badge/Docker%20Pull-anoxiayu%2Flottery--bot-blue.svg?style=flat-square&logo=docker&logoColor=white)](https://hub.docker.com/repository/docker/anoxiayu/lottery-bot/general)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg?style=flat-square)](https://opensource.org/licenses/MIT)

> **专为 NAS 打造的自动化彩票管家**
>
> 📦 **Docker Hub 官方镜像:** [**anoxiayu/lottery-bot**](https://hub.docker.com/repository/docker/anoxiayu/lottery-bot/general)

基于 Python Flask 构建的大乐透中奖情况查询与定时推送服务。专为威联通 (QNAP)、群晖 (Synology) 及 Docker 环境设计。支持多用户管理、多期连买自动核对、历史中奖查询以及 Server酱微信推送。

---

## ✨ 核心亮点

*   **📊 全自动兑奖**：每日定时同步官方开奖数据（周一/三/六），自动计算中奖等级与金额（支持一等奖至九等奖所有奖级）。
*   **🎫 多期智能管理**：支持设置"开始期号"与"连买期数"（1-30期），系统自动计算有效期，过期自动归档。
*   **📲 隐私推送**：集成 [Server酱](https://sct.ftqq.com/)，支持 Key 前端打码显示。仅在开奖日且在有效期内推送。
*   **🕒 自定义计划**：支持在网页端直接修改每日自动检查的时间（默认 22:00），无需重启容器。
*   **📜 历史回溯报告**：一键生成某注号码在过去 50 期内的中奖情况汇总，并推送到微信。
*   **🎨 拟态 UI 设计**：采用精美的 3D 拟态风格圆球设计，完美适配手机端操作。
*   **🔐 数据安全**：支持多用户隔离，支持 Docker 挂载持久化存储，重启不丢失数据。

---

## 🆕 v10 版本功能

### 核心功能

*   **👤 管理员后台**：第一个注册用户自动成为管理员，可管理所有用户、审核新用户、禁用/删除账户。
*   **✅ 新用户审核**：新注册用户需管理员审核后才能登录使用。
*   **🔑 密码找回**：用户可申请密码重置，管理员审核后生效。
*   **📷 智能拍照识别**：支持拍照/上传图片，集成 **RapidOCR** 自动识别彩票号码和期数。
*   **🎲 随机号码生成**：一键生成随机大乐透号码组合。
*   **🎮 模拟购彩**：支持模拟购彩模式，不计入统计，方便练习。
*   **🗑️ 过期自动删除**：可选开启自动删除过期彩票功能。
*   **✏️ 彩票编辑**：支持修改已添加的彩票号码和期数。
*   **📱 移动端适配**：管理后台完美适配手机端显示。

### 智能文档扫描 (v10 新增)

*   **📐 自动透视矫正**：类似全能扫描王，自动检测彩票边缘并进行透视变换矫正
*   **🔍 边缘检测**：基于 Canny 边缘检测与轮廓近似算法，精准识别彩票区域
*   **✨ 图像增强**：自动调整对比度、锐化处理，提升 OCR 识别精度
*   **📱 EXIF 修正**：自动修复手机拍照的图片旋转问题

---

## 🧠 OCR 识别技术

本项目集成 **RapidOCR**（基于 PaddleOCR 引擎），采用**智能文档扫描+图像增强**技术，大幅提升彩票号码识别准确率。

### 特点

- ✅ **内置集成**：无需单独安装 OCR 软件
- ✅ **离线识别**：不需要网络，本地处理
- ✅ **高精度**：基于 PaddleOCR，中文识别效果好
- ✅ **智能矫正**：自动检测彩票边缘并透视矫正
- ✅ **Docker 支持**：容器自动包含所有依赖

### 识别流程

| 步骤 | 说明 | 技术 |
|------|------|------|
| **1. 图片修正** | 自动修复 EXIF 旋转 | ImageOps.exif_transpose |
| **2. 边缘检测** | 检测彩票矩形轮廓 | Canny + findContours |
| **3. 透视矫正** | 四点变换拉平图像 | getPerspectiveTransform |
| **4. 图像增强** | 灰度+对比度+锐化 | PIL ImageEnhance |
| **5. OCR识别** | 文字检测与识别 | RapidOCR |
| **6. 智能解析** | 号码提取与验证 | 正则+语义分析 |

### 智能解析能力

*   **号码拆分**：自动拆分粘连数字（如 `0304` → `03`, `04`）
*   **期号识别**：自动识别 "第xxxxx期" 格式的期号
*   **连买期数**：自动识别购买期数信息
*   **跨行拼接**：支持红球和蓝球分行打印的情况
*   **噪声过滤**：自动过滤开奖、合计、金额等干扰信息

---

## 🚀 快速部署 (Docker)

无需下载源码，直接拉取镜像即可运行。

### 1. 准备工作
在您的 NAS 或服务器上创建一个用于存放数据的文件夹，例如：`/share/Container/lottery_bot/data`。

### 2. 启动容器 (命令行/SSH)

```
# 1. 拉取最新镜像
docker pull anoxiayu/lottery-bot:latest

# 2. 启动容器 (请务必修改 -v 挂载路径为您的实际路径)
docker run -d \
  --name lotto-web \
  -p 5000:5000 \
  -v /share/Container/lottery_bot/data:/app/data \
  --restart unless-stopped \
  anoxiayu/lottery-bot:latest
```

*   **`-p 5000:5000`**: 访问端口。
*   **`-v ...:/app/data`**: **[⚠️重要]** 数据持久化目录。如果不挂载，重启后账号和彩票数据将**丢失**。
*   **`--restart unless-stopped`**: 保证 NAS 重启后服务自动启动。

### 3. 威联通 Container Station 部署

1.  打开 **Container Station**。
2.  点击 **Images (镜像)** -> **Pull (拉取)**。
3.  输入镜像名称：`anoxiayu/lottery-bot`，版本：`latest`。
4.  拉取完成后点击 **Create (创建)**。
5.  **Advanced Settings (高级设置)**：
    *   **Network**: Host Port `5000` -> Container Port `5000`。
    *   **Shared Folders**: 新增挂载，将本机文件夹映射到容器内的 `/app/data`。

---

## 📖 使用指南

1.  **访问**：浏览器打开 `http://<NAS_IP>:5000`。
2.  **注册**：首次使用请点击“去注册”创建账号。
3.  **配置**：
    *   登录后，在主页底部的“系统设置”卡片中填入 **Server酱 SendKey**。
    *   设置自动推送时间（建议设置为 `21:40` 或 `22:00`，以防官方接口延迟）。
    *   点击“保存”，Key 会自动打码保护。
4.  **添加号码**：
    *   点击 **`+ 添加`**，输入前区/后区号码。
    *   **连买期数**：默认为1，支持修改，系统会自动计算结束期号。
5.  **状态说明**：
    *   🟦 **待开奖**：当前期号未达到开始期号。
    *   🟨 **已中奖**：显示中奖等级及金额。
    *   ⬜ **已过期**：当前期号超过结束期号。

---

## 🛠️ 手动构建 

如果您需要修改源码进行二次开发：

```
# 1. 下载源码
git clone <your-repo-url>
cd lottery-bot

# 2. 构建镜像
docker build -t my-lotto-local .

# 3. 运行
docker run -d -p 5000:5000 -v $(pwd)/data:/app/data my-lotto-local
```

---

## ⚖️ 开源协议 (License)

本项目采用 **MIT 许可证** 开源。您可以自由地使用、修改和分发，但需保留版权声明。

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

<div align="center">
    <small>Lottery Assistant v10 | Powered by Flask & Docker | Made by Anoxiayu</small>
</div>