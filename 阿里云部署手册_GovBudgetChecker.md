# GovBudgetChecker 阿里云生产环境部署手册

本文档为在阿里云 ECS 服务器上从零部署 GovBudgetChecker 系统的一套完整且详细的步骤指南。

## 第一阶段：阿里云服务器准备工作

### 1. 购买并初始化 ECS 云服务器
1. **操作系统**：推荐使用 **Ubuntu 22.04 LTS** 64位（兼容性最好，教程最通用）。
2. **实例规格**：建议至少分配 2核 CPU 和 4GB 内存（系统包含 AI Extractor、Backend、Frontend 多个容器，资源过低可能导致容器 OOM 被杀）。
3. **公网 IP**：勾选分配公网 IPv4 地址。

### 2. 配置安全组策略
登录阿里云控制台，进入该 ECS 实例的安全组规则，放行出入站规则中的关键端口：
* **22** 端口：SSH 远程登录必需。
* **80** 端口：HTTP Web 服务（如使用 Nginx 反代 frontend）。
* **443** 端口：HTTPS 访问（推荐生产使用）。
* *(可选但在调试期推荐)* **3001** 端口：Docker Frontend 直接访问端口。
* *(可选但在调试期推荐)* **18743** 端口：Backend API 默认宿主机访问端口（可用 `GOVBUDGET_BACKEND_PORT` 修改；容器内仍为 8000）。
* *(可选但在调试期推荐)* **9009** 端口：AI-Extractor 监控端口。

---

## 第二阶段：服务器基础环境安装

使用 SSH 工具（如 Xshell, Termius, Mac 终端）连接到你的阿里云服务器。

### 1. 更新系统包
```bash
sudo apt update && sudo apt upgrade -y
```

### 2. 安装 Git、Curl 工具
```bash
sudo apt install git curl vim ufw -y
```

### 3. 安装 Docker
```bash
# 下载 Docker 官方的自动安装脚本
curl -fsSL https://get.docker.com -o get-docker.sh

# 执行安装
sudo sh get-docker.sh

# 启动 docker 并设置开机自启
sudo systemctl enable docker
sudo systemctl start docker

# （可选）将当前用户加入 docker 组以免每次都需要 sudo
sudo usermod -aG docker $USER
# 配置生效可能需要重新登录 SSH，建议登出后重新连上。
```

### 4. 安装 Docker Compose v2
Docker Compose v2 现在默认作为 Docker 的一个插件包含在官方安装包里。通过以下命令验证是否安装成功：
```bash
docker compose version
```

---

## 第三阶段：拉取代码与配置项目

### 1. 获取代码到服务器
因为通常我们的代码放在 GitHub 或自建 Gitlab 上，可以在服务器上克隆仓库。若没有公网仓库，可以直接通过 `scp` 等方式将本地含有所有代码的（去掉 node_modules、__pycache__等缓存）的包上传至服务器 `/opt/GovBudgetChecker` 目录下。

假设你走 Git 方式：
```bash
# 创建部署目录
sudo mkdir -p /opt/GovBudgetChecker
sudo chown -R $USER:$USER /opt/GovBudgetChecker
cd /opt/GovBudgetChecker

# clone 代码
git clone <你的仓库地址> .
```

### 2. 配置环境变量
项目提供了 `.env.example`。我们需要将它复制一份为真实的 `.env` 并按需修改。

```bash
cd /opt/GovBudgetChecker
cp .env.example .env
vim .env
```

**在 `.env` 中，重点修改以下部分内容：**
```ini
# --- 安全与认证 ---
GOVBUDGET_AUTH_ENABLED=true
# 必须：改成你自己设定的强密钥
GOVBUDGET_API_KEY=your_production_secure_api_key_here

# --- 大模型配置（若使用火山引擎豆包） ---
ARK_API_KEY=你的火山引擎_API_KEY
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=doubao-1-5-pro-32k-250115

# 若你使用的是 Gemini 代理或者其他兼容的大模型，请设置相对应的环境变量：
# 例如 OPENAI_API_KEY=xxx / OPENAI_BASE_URL=xxx / OPENAI_MODEL=xxx
```

### 3. 创建必须的数据存储挂载目录
项目依赖一些本地的持久化目录。建议预先创建，以防止 Docker 使用 `root` 权限去创建导致的后续权限问题：
```bash
mkdir -p data logs samples uploads
```

---

## 第四阶段：一键启动与部署验证

### 1. 构建并启动容器
默认完整服务栈为 `postgres`、`ocr`、`backend`、`worker`、`frontend`，
统一定义在 `docker-compose.yml`。系统设置中配置的大模型直接调用其 API；
仅需要独立 AI 抽取器时才叠加 `docker-compose.ai.yml`。
其中 `backend` 只入队（`JOB_QUEUE_ROLE=api`）、`worker` 负责消费队列（`JOB_QUEUE_ROLE=worker`），
两者共享同一个 `uploads` 卷；缺了 `worker`，任务只会一直停在 `queued`。

请在项目根目录（`/opt/GovBudgetChecker`）运行：
```bash
bash scripts/start-server.sh
```
首次部署须填写 `.env` 的数据库、会话密钥和管理员配置，并将完整离线 OCR 模型包
放到 `models/`，将 `models/MANIFEST.sha256` 的 SHA-256 填入 `OCR_MODEL_FINGERPRINT`。
启动入口等待数据库、OCR、Worker 与前端就绪后才报告成功。
只检查配置使用 `bash scripts/start-server.sh --check`；使用已有镜像可加 `--no-build`。
需要独立 AI 抽取器时使用 `bash scripts/start-server.sh --ai`。
*注：由于是在国内阿里云服务器部署，如果你未配置 Docker 国内加速镜像源，`build` 过程下载 python/node 的基础镜像可能会稍慢，请耐心等待。*

### 2. 查看容器状态和日志
观察所有容器是否都是 `Up` (或者 `healthy`) 状态：
```bash
docker compose -f docker-compose.yml ps
```

检查 `backend` 的运行日志（非常重要，它是核心业务）：
```bash
docker compose -f docker-compose.yml logs -f backend worker
```
> 应结合 `healthy` 状态及 `/ready` 验证依赖，不能仅凭没有报错日志判断就绪。

### 3. 可用性冒烟测试
可以直接在服务器上请求一下本地的健康检查接口验证服务连通性：
```bash
# 测试后端健康状态
curl -fsS http://localhost:18743/ready

# 测试前端连通性
curl -fsS http://localhost:3001/api/health
```
返回 200 表示当前核心服务链路就绪；业务效果仍需上传实际材料验证。

---

## 第五阶段：生产级优化 (可选，但极度推荐)

网页宿主机端口为 `3001`，后端宿主机端口为 `18743`。需要域名访问时，可以部署
**Nginx** 作为主入口并配置 HTTPS；数据库的 `5432` 只绑定本机。

### 1. 安装 Nginx
```bash
sudo apt install nginx -y
```

### 2. Nginx 配置示例
假设你已经将域名 `yourdomain.com` 解析到了阿里云的公网 IP。
```bash
sudo vim /etc/nginx/sites-available/govbudget
```

填入以下内容（如果是 HTTP：）
```nginx
server {
    listen 80;
    server_name yourdomain.com;

    # 代理到前端页面
    location / {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_cache_bypass $http_upgrade;
    }

    # （可选）代理到后端API，假如前端是同一个域名，API可以放在 /api/ 下路由
    # 需要注意在之前前端的环境变量里配置好外部调用的正确路径。
    # location /api/ {
    #     proxy_pass http://127.0.0.1:18743/api/;
    #     proxy_set_header Host $host;
    # }
}
```

启用该配置并重启 Nginx：
```bash
sudo ln -s /etc/nginx/sites-available/govbudget /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

### 3. 配置 HTTPS (Let's Encrypt)
如果你有域名，可以通过 `certbot` 免费签发 HTTPS 证书。
```bash
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d yourdomain.com
```
按照提示操作即可，它会自动帮你修改上面的 nginx 配置文件去适配 443 端口！

---
到此为止，你便完成了基于阿里云服务器部署的全部流程！🎉
