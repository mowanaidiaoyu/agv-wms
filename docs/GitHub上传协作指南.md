# 项目上传 GitHub 与三人协作指南

> 适用：Windows。本文档里的命令在 **Git Bash** 里跑。
> 仓库根目录是 `C:\Users\24540\Desktop\WMS\web`（下面统一用 `web` 表示）。

---

## 0. 现状（已完成，不用重复做）

- 仓库已上线：**`https://github.com/mowanaidiaoyu/agv-wms`**（owner `mowanaidiaoyu`）。
- `main` 分支已推送成功，代码都在上面。
- 推送用的是 **SSH**（不是 HTTPS），因为国内直连 GitHub 的 HTTPS 端口被墙（会报 `Recv failure: Connection was reset`），SSH 走 443 端口稳定可用。
- 已忽略：`.venv/`、`node_modules/`、`dist/`、`app_data/`（SQLite 库）、`__pycache__/`、`test-results/` 等。
- 仓库外内容（不进 git）：`WMS\data\`（约 187MB 源数据，走网盘同步）、`WMS\xiugai\`（修改清单）、`WMS\启动方法.md`。

---

## 1. 网络准备：改用 SSH（国内三人必做，各做一次）

直连 GitHub 的 HTTPS 端口会被墙，所以**三人都要**改用 SSH 方式连接 GitHub。只需做一次。

### 1.1 设置提交身份（各设一次）

```bash
git config --global user.name  "你的名字"
git config --global user.email "你的GitHub邮箱"
```

### 1.2 生成 SSH 密钥（各做一次）

```bash
ssh-keygen -t ed25519 -C "你的GitHub邮箱"
```

- 一路回车（保存位置用默认 `C:\Users\你\.ssh\id_ed25519`，密码留空即可）。
- 然后**强制走 443 端口**（国内最稳），把下面内容写进 `C:\Users\你\.ssh\config`（没有这个文件就新建）：

```
Host github.com
  HostName ssh.github.com
  Port 443
  User git
  IdentityFile ~/.ssh/id_ed25519
```

### 1.3 把公钥加到 GitHub（各做一次）

查看并复制公钥：

```bash
cat ~/.ssh/id_ed25519.pub
```

> 复制整行（从 `ssh-ed25519` 开头到邮箱结尾）。

GitHub 网页（登录各自账号）→ 右上角头像 → **Settings** → 左侧 **SSH and GPG keys** → **New SSH key** → Title 随便填、Key type 选 **Authentication Key**、Key 粘贴 → **Add SSH key**。

### 1.4 验证（各做一次）

```bash
ssh -T git@github.com
```

看到 `Hi <你的用户名>! You've successfully authenticated...` 就说明通了。

---

## 2. 邀请另外两个人（仓库 owner `mowanaidiaoyu` 做）

1. GitHub 打开 `https://github.com/mowanaidiaoyu/agv-wms` → **Settings → Collaborators → Add people**。
2. 输入他们的 GitHub 用户名，发邀请：
   - `fengyouyinli1314`（1732129251@qq.com）
   - `19170645119`（3231591517@qq.com）
3. 对方在邮件 / GitHub 通知里点**接受**。

---

## 3. 协作者第一次克隆与启动

```bash
git clone git@github.com:mowanaidiaoyu/agv-wms.git
cd agv-wms

# 后端（Windows / PowerShell）
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt

# 前端
cd frontend
npm install
npm run dev
```

> 数据准备：把 `data` 源文件（.dxf/.dwg/.xlsx）放到**仓库上一级**的 `data\` 目录（与 `agv-wms` 同级），后端从 `../data` 读取。数据下载方式见群共享网盘（`docs\启动方法.md`）。

---

## 4. 日常协作流程（推荐，3 人）

### 4.1 保护主分支（只做一次，owner 做）

GitHub → 仓库 **Settings → Branches → Branch protection rules → Add rule**：

- Branch name pattern：`main`
- 勾选 **Require a pull request before merging**
- 勾选 **Require approvals**（至少 1 人）

### 4.2 每个人每次做功能

```bash
git checkout main
git pull origin main          # 先同步最新
git checkout -b feature/xxx    # 开自己的功能分支
# ……改代码……
git add -A
git commit -m "feat: xxx"
git push origin feature/xxx
```

然后到 GitHub 上开 **Pull Request**，让另一个人 review 后合并，删掉功能分支。

### 4.3 分工建议

- 每人认领一个模块（前端 / 后端 / 拓扑编辑器等），尽量不碰同一文件，减少冲突。
- 改公共文件（如 `types.ts`、`schemas.py`）前先在群里说一声。

---

## 5. 冲突处理

```bash
git checkout main
git pull origin main          # 若提示冲突，会告诉你哪些文件冲突
# 打开冲突文件，找到 <<<<<<< / ======= / >>>>>>> 手动合并
git add 冲突文件
git commit -m "merge: 解决冲突"
git push origin main
```

---

## 6. 注意事项清单（踩坑提醒）

1. **不要提交**：`.venv/`、`node_modules/`、`backend/app_data/`（SQLite 数据库）、`frontend/dist/`、`__pycache__/`、`test-results/`。提交前先 `git status` 看一眼。
2. **数据库是本地生成的**：队友 clone 后要自己跑 seed 脚本生成 `app_data/agv_platform.db`，别指望 git 里带数据库。
3. **大文件**（`data\*.dxf / *.dwg`）**别 git add**，走网盘同步，路径保持 `仓库上一级\data\`。
4. **依赖一致**：提交 `package-lock.json` 和 `backend/requirements.txt`。
5. **提交前先 pull**：`git pull origin main` 再 push，能少一半冲突。
6. **别直接改 main**：日常都走功能分支 + PR（第 4 节）。
7. **提交信息写清楚**：`feat: 加了什么 / fix: 修了什么`。
8. **推送报 `Connection was reset`**：说明还在用 HTTPS 或没配好 SSH，回到第 1 节检查。
