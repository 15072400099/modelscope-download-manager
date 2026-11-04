# 参与贡献

先谢谢你愿意花时间。这个项目很小，规矩不多，但有几条**硬性约定**必须先看。

## 硬性约定

### 1. `.bat` 文件必须是 GBK 编码

Windows 的 `cmd.exe` 按系统 ANSI 代码页（简体中文是 GBK/936）解析批处理。
用 UTF-8 保存会直接报「'到' 不是内部或外部命令」这种错。

改完批处理请确认：

```powershell
[System.IO.File]::ReadAllText('启动下载管理器.bat', [Text.Encoding]::GetEncoding(936))
```

能正常显示中文就说明编码没坏。`.gitattributes` 里已经把 `*.bat` 标记为 `-text`，
git 不会去转换它的换行和编码。

### 2. 不要提交这些东西

`models/`、`data/`、`python/`、`.venv/`、`build/`、`dist/` 都在 `.gitignore` 里，
别用 `git add -f` 绕过。模型动辄几十 GB。

### 3. 只监听本机

服务只能绑 `127.0.0.1`。这个程序没有鉴权，却带有删除文件夹的接口，
一旦监听 `0.0.0.0` 等于把删文件的权限开放给整个局域网。**不要改这一点。**

## 开发环境

需要 Windows + Python 3.10 以上：

```bat
安装环境.bat
启动下载管理器.bat
```

也可以直接用系统 Python：

```bat
python -m pip install -r requirements.txt
python app\server.py
```

打开 <http://127.0.0.1:8765> 就是界面。

## 代码结构

```
app/server.py            HTTP 服务与路由（标准库，无框架）
app/download_manager.py  任务队列、进度、暂停续传、本地模型库
app/ms_client.py         ModelScope 接口封装与错误翻译
app/web/                 前端（原生 JS，无构建步骤）
app/verify_models.py     和远端清单比对，检查文件是否齐全
app/repair_missing.py    只补缺失的分片（HTTP Range 续传）
```

改前端不需要重启服务，刷新页面即可；改后端要重启。

## 提交前自测

- [ ] 能启动，界面能打开
- [ ] 搜索能出结果
- [ ] 下载一个小模型（如 `Qwen/Qwen2.5-0.5B-Instruct` 的 `config.json`）能完成
- [ ] 下载中点「暂停」再点「继续」，进度没有归零
- [ ] `python app\verify_models.py` 能正常输出报告

## 提交信息

用 [约定式提交](https://www.conventionalcommits.org/zh-hans/) 前缀：

```
feat: 增加按文件大小筛选
fix: 修复暂停后进度条不刷新
docs: 补充 exe 打包说明
```

## 关于依赖

`modelscope` 的内部接口不是公开契约，升级大版本可能直接让下载路径失效。
改动涉及下载时，请把 `modelscope>=1.40,<2` 的版本范围一起考虑，
并在 PR 里写清楚你测试用的版本。
