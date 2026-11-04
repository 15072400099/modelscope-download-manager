# 开发与反馈说明

## 这个项目不接收代码修改

**Pull Request 不会被合并。** 这不是客套话，是真的不合并，避免你白写。

欢迎的：

- 提 [Issue](https://github.com/15072400099/modelscope-download-manager/issues/new/choose) 说清遇到的问题（下载失败、界面报错、文件缺失、某个模型下不了）
- 使用体验上的建议：哪个按钮多余、哪句提示看不懂、哪里该有确认框
- 兼容性反馈：你在哪台机器、哪个 Windows 版本上跑出了什么现象

不接收的：

- 代码 PR（包括"顺手修个 typo"）
- Dependabot 之类的自动依赖升级 PR

### 为什么

- 这个项目刻意保持「一个人的形状」：代码风格、错误文案、界面语气都是一套连贯的取舍，多人改动会散
- 它依赖 ModelScope 的非公开接口，任何第三方改动都很难长期验证兼容性
- 作者只在自己的机器和少数环境上验证过，维护不了别人改出来的分支

想自己改着用完全可以：MIT 许可允许你 fork 任意修改，只是不会合并回本仓库。

---

## 如果你要自己在本地跑起来改

需要 Windows + Python 3.10 以上。

```bat
安装环境.bat
启动下载管理器.bat
```

或者直接用系统 Python：

```bat
python -m pip install -r requirements.txt
python app\server.py
```

界面在 <http://127.0.0.1:8765>。改前端不用重启（刷新页面），改后端要重启。

### 代码结构

```
app/server.py            HTTP 服务与路由（标准库，无框架）
app/download_manager.py  任务队列、进度、暂停续传、本地模型库
app/ms_client.py         魔搭接口封装（纯 HTTP）与错误翻译
app/transport.py         HTTP 传输层（调用系统 curl）
app/web/                 前端（原生 JS，无构建步骤）
app/verify_models.py     和远端清单比对，检查文件是否齐全
app/repair_missing.py    只补缺失的分片（HTTP Range 续传）
```

### 三个必须知道的坑

**1. `.bat` 文件必须是 GBK 编码。**
`cmd.exe` 按系统 ANSI 代码页（简体中文是 GBK/936）解析批处理，存成 UTF-8 会直接报
「'到' 不是内部或外部命令」。改完用这个确认：

```powershell
[System.IO.File]::ReadAllText('启动下载管理器.bat', [Text.Encoding]::GetEncoding(936))
```

能正常显示中文就说明没坏。`.gitattributes` 已把 `*.bat` 标记为 `-text`，git 不会转换它的字节。

**2. 不要提交 `models/`、`data/`、`python/`、`.venv/`、`build/`、`dist/`。**
都在 `.gitignore` 里，别用 `git add -f` 绕过。模型动辄几十 GB。

**3. 只监听 `127.0.0.1`。**
这个程序没有鉴权，却带有删除文件夹的接口。改成 `0.0.0.0` 等于把删文件权限开放给整个局域网。

## 关于依赖

**这个项目是零第三方依赖的**，请保持：只用 Python 标准库 + 系统自带的 `curl.exe`。
CI 里有一条「零依赖检查」，一旦出现 `import requests` / `import modelscope` 就会红。

传输层在 `app/transport.py`，改动时注意两点：

- Windows 自带的 curl 8.4 不带 zlib，**不能用 `--compressed`**
- 魔搭的 REST 接口不是公开契约，对方改了要跟着改；改动前先跑一遍
  `app/verify_models.py` 确认接口还通