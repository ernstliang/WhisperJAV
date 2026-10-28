# WhisperJAV Web 界面指南

> 本机浏览器界面，用于转写与翻译 · v1.8.14

Web 界面在你自己的机器上起一个页面：浏览本地文件、配置任务、启动、看实时进度。
它和 `whisperjav-gui` 不是一回事——后者是原生桌面窗口。

| | Web 界面（`whisperjav-web`） | 桌面 GUI（`whisperjav-gui`） |
|---|---|---|
| 形态 | 本地 HTTP 服务，用浏览器打开 | 原生 PyWebView 窗口 |
| 选文件 | 浏览**服务端**磁盘 | 系统原生文件对话框 |
| 可达范围 | 本机任意浏览器 | 仅桌面会话 |

---

## 目录

1. [快速开始](#1)
2. [脚本命令](#2)
3. [什么时候需要重启](#3)
4. [三种任务类型](#4)
5. [端口与参数](#5)
6. [安全](#6)
7. [排查](#7)

---

## 1. 快速开始 { #1 }

仓库根目录有两个启动脚本，它们按自身所在位置定位虚拟环境，所以在哪儿调用都行。

=== "Windows"

    双击 **`start-web.bat`**，或在终端里：

    ```bat
    start-web.bat
    ```

=== "Git Bash / Linux / macOS"

    ```bash
    ./start-web.sh
    ```

浏览器会自动打开 <http://127.0.0.1:8317/>。在终端按 ++ctrl+c++ 停止服务。

也可以完全不用脚本：

```bash
whisperjav-web                                  # 已安装包时
python -m whisperjav.webserver --port 8317      # 源码检出时
```

脚本相比裸命令多做三件事：替你找到 venv；已在运行时复用实例而不是抢端口；有任务在跑时拒绝停服务。

---

## 2. 脚本命令 { #2 }

```
start-web.bat                 启动。已在运行则复用。
start-web.bat restart         停掉正在运行的实例，然后启动。
start-web.bat stop            只停止，然后退出。
start-web.bat --port 9000     其余参数原样透传给 whisperjav-web。
```

`./start-web.sh` 支持同样的动词。

**复用**：服务已经起着时再跑脚本，不会起第二个，只会打印地址并打开浏览器。

**停止有保护**：`stop` 和 `restart` 会先查 `/api/jobs`。如果有任务在跑，脚本会警告并要求确认——因为关停服务会终结整个任务进程树：

```
[WARNING] A job is still running on this server.
          Stopping now would kill it and lose the work in progress.
Stop anyway? [y/N]
```

回答 `y` 以外的任何内容都会中止操作，服务和任务都不受影响。

!!! warning "任务历史不落盘"
    任务只存在内存里。停止服务会清空历史，并取消所有排队中和运行中的任务。没有断点续跑。

---

## 3. 什么时候需要重启 { #3 }

大部分改动**不需要**重启，因为每个任务都是新起的 CLI 子进程。

| 改动内容 | 需要重启？ |
|---|---|
| 再跑一个任务 | **不用** —— 直接点「开始」 |
| 管线代码（`whisperjav/main.py`、`pipelines/`、`translate/`、`utils/`） | **不用** —— 下一个任务会加载新代码 |
| 前端（`webserver/static/*.html`、`*.js`、`*.css`） | **不用** —— 刷新页面（看着没变就 ++ctrl+f5++） |
| Web 服务代码（`whisperjav/webserver/*.py`） | **需要** —— `start-web.bat restart` |
| `pyproject.toml` | **需要**，而且要重装 |

这里刻意没有提供 `--reload`。uvicorn 的热重载把任务挂在会被杀掉的工作进程下，重载时会静默摧毁正在跑的长任务。

---

## 4. 三种任务类型 { #4 }

用页面顶部的单选按钮切换。

| 模式 | 输入 | 实际执行 |
|---|---|---|
| **视频转写出 SRT** | 视频 / 音频 | `whisperjav.main` → SRT |
| **已有 SRT 直接翻译** | 现成的 `.srt` | `whisperjav.translate.cli` → 译文 SRT |
| **转写 + 自动翻译** | 视频 / 音频 | **单个** `whisperjav.main --translate` 进程 |

用「浏览…」按钮选文件，浏览的是本机自己的磁盘——不上传，所以选 40 GB 的视频也没有成本。用「添加整个文件夹」可以把目录里所有匹配的文件一次加进来。

进度面板显示总进度百分比、当前文件、当前步骤、子任务计数与 ETA，以及最后一条日志。下面的日志控制台实时输出进程内容。任务结束后产出文件路径会列出来，带复制按钮。

翻译时「模型」可以留空，实际生效的模型按 **表单输入 → `settings.json` → provider 内置默认** 解析，输入框下方的提示会告诉你这次到底会用哪个。

---

## 5. 端口与参数 { #5 }

默认端口 **8317**，两种方式可改：

=== "命令行参数"

    ```bat
    start-web.bat --port 9000
    ```

=== "环境变量"

    ```bat
    set WHISPERJAV_WEB_PORT=9000
    start-web.bat
    ```

端口被占用时，`whisperjav-web` 会往后试 9 个端口，并打印实际绑定的那个。

完整参数：

| 参数 | 含义 |
|---|---|
| `--host` | 绑定地址，默认 `127.0.0.1` |
| `--port` | 默认 `8317`，被占用时自动顺延 |
| `--no-open-browser` | 启动时不打开浏览器 |
| `--log-level` | `critical` / `error` / `warning` / `info` / `debug` |
| `--allow-remote` | 绑定非环回地址时必须显式指定，见下 |

---

## 6. 安全 { #6 }

服务只绑 `127.0.0.1`，**没有任何鉴权**，而且它的目录浏览能读到当前用户能读的所有位置。所以要绑到可达地址必须显式开口：

```
$ whisperjav-web --host 0.0.0.0
Refusing to bind 0.0.0.0.
This server has no authentication and /api/browse can read your entire
filesystem.
Re-run with --allow-remote if that is really what you want.
```

不要把它暴露到不受信任的网络。

翻译表单里填的 API Key 不会写进浏览器存储；界面上的命令预览会把它打码成 `--translate-api-key ***`。

---

## 7. 排查 { #7 }

**提示 "Virtual environment not found"**

脚本在自己旁边没找到 `.venv`。在仓库根目录创建：

```bash
uv sync --python 3.12 --extra all
```

**页面打不开**

先确认服务真的在应答：

```bash
curl http://127.0.0.1:8317/api/health
```

正常会返回 `{"ok":true,"version":"1.8.14","pid":...}`。如果它有应答但浏览器打不开，确认浏览器就跑在这台机器上——`127.0.0.1` 按设计就是别的主机访问不到的。

**一个服务对应两个 `python.exe` 进程**

在 uv 创建的虚拟环境上这是正常的。`.venv\Scripts\python.exe` 是个 trampoline，会用同样的参数 exec 真实解释器，所以你会看到一父一子。只有其中一个持有监听套接字：

```powershell
Get-NetTCPConnection -LocalPort 8317 -State Listen
```

`stop` 按命令行匹配，两个一起清掉。

**停了之后端口还被占**

通常是有实例没被完全杀掉。`start-web.bat stop` 会验证端口确实释放，没释放会报错。要手工查看：

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*whisperjav.webserver*' }
```

**任务失败，提示 "Process crashed: ..."**

这是加载的原生库内部的硬件级异常，不是 Python 错误，所以没有 traceback。解码后的异常名和最后到达的步骤会写在任务的错误信息和日志末尾。任务都带 `PYTHONFAULTHANDLER=1` 运行，所以崩溃瞬间的 Python 调用栈也会打进日志。

**进度条长时间不动**

百分比按约 10% 的里程碑跳变，这是设计如此；单个长场景或下载模型都可能让它平几分钟。已用时间和最后一条日志是一直在走的——用这两个来判断是慢还是卡死。
