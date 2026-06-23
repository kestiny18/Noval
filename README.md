# Noval — 通用 Agent 小核心

[![CI](https://github.com/kestiny18/Noval/actions/workflows/ci.yml/badge.svg)](https://github.com/kestiny18/Noval/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)

一个与场景解耦的通用 agent 内核。设计原则见 [AGENTS.md](AGENTS.md)，决策记录见 [DESIGN.md](DESIGN.md)。

## 在一台新电脑上运行

### 0. 拿到代码
把整个 `Noval/` 文件夹拷过去即可（U 盘 / 网盘 / 或用 git）。

### 1. 装 Python
需要 **Python 3.10+**。`python --version` 能打印版本即可。
> Windows 上如果 `python` 打开了应用商店或无输出，用 `py` 代替下面所有的 `python`。

### 2.（推荐）建虚拟环境
```bash
# 在项目根目录 Noval/ 下
python -m venv .venv

# 激活：
#   Windows PowerShell:  .venv\Scripts\Activate.ps1
#   Windows cmd:         .venv\Scripts\activate.bat
#   macOS / Linux:       source .venv/bin/activate
```

### 3. 装依赖
```bash
pip install -r requirements.txt
```

### 4. 设置 API key（二选一，绝不写进仓库代码）

**方式 A：写进配置文件（一次性，换终端也不丢）** —— 推荐日常用。
在 `~/.noval/settings.json`（Windows 是 `C:\Users\<你>\.noval\settings.json`）里加一行：
```json
{ "api_key": "sk-你的key" }
```
该文件在主目录、**不在仓库里**，所以分享/提交项目代码不会泄露 key。

**方式 B：环境变量（只在当前终端有效）**
```bash
# Windows PowerShell:  $env:DEEPSEEK_API_KEY="sk-你的key"
# Windows cmd:         set DEEPSEEK_API_KEY=sk-你的key
# macOS / Linux:       export DEEPSEEK_API_KEY="sk-你的key"
```

> 解析优先级：`settings.json` 的 `api_key` → 环境变量 `DEEPSEEK_API_KEY` → 都没有则报错。
> ⚠️ 别把 key 写进仓库内的 `settings.example.json`；它只该出现在主目录的 `~/.noval/settings.json` 里。

### 5. 运行
```bash
# 必须在项目根目录 Noval/ 下运行，这样 noval 包才能被导入
python -m noval

# 工作目录默认是当前启动目录；要指定就用 --workdir：
python -m noval --workdir C:/path/to/your/project
```
看到 `Noval 已就绪 (workdir: ...)。输入 'exit' 退出。` 就成了。
> 工作目录是「本次启动」的状态，不写进 settings.json——同时跑多个实例时各指各的项目，互不影响。

### 6.（可选）自定义配置
默认值无需配置即可用。要改就在用户目录下创建 `~/.noval/settings.json`
（Windows 是 `C:\Users\<你>\.noval\settings.json`），内容参考仓库里的
[settings.example.json](settings.example.json)。文件缺失时一律走默认值。

## 跑测试
```bash
pip install pytest        # 测试才需要，运行本体不需要
python -m pytest          # 应输出 69 passed, 2 skipped
```

## 常见问题
- **`ModuleNotFoundError: No module named 'noval'`** → 没在项目根目录 `Noval/` 下运行。
- **退出并提示「未找到 API key」** → 第 4 步的环境变量没设，或新开了终端导致变量丢失（环境变量只在当前终端会话有效）。
- **`openai` 找不到** → 第 3 步没装依赖，或虚拟环境没激活。

## 内置工具

| 工具 | 风险 | 说明 |
|------|------|------|
| `read_file` | READ | 带行号读取，支持 offset/limit 翻页 |
| `list_directory` | READ | 列目录 |
| `glob` | READ | 文件名模式查找（按修改时间排序） |
| `grep` | READ | 内容正则搜索（files/content/count 三种模式，排除 .git） |
| `write_file` | WRITE | 写文件；改已存在文件须先 read |
| `edit_file` | WRITE | 精确字符串替换（唯一匹配或 replace_all） |
| `run_bash` | DANGEROUS | 在 workdir 下执行命令（默认需确认） |

文件工具共享一套「read-tracker」状态机：改前须先读、检测文件被外部改动。设计见 [DESIGN.md](DESIGN.md) 决策 10/11。

## 项目记忆（AGENTS.md）

在项目根目录放一个 `AGENTS.md`（[开放标准](https://agents.md/)，纯 Markdown），写上构建/测试命令、代码规范、约定等，Noval 启动时会自动读取并注入上下文（没有 `AGENTS.md` 则回退读 `CLAUDE.md`）。

- 它是**项目级约定**，不会覆盖 Noval 的安全行为（如危险命令确认门）。
- **启动读一次快照**——改了 `AGENTS.md` 需重启生效。
- 内容要精炼、高信号（自动生成的冗长 context 反而有害）。Noval **只读不写**它。

## 贡献 & 许可证

- 想加工具 / 提改动，先看 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [AGENTS.md](AGENTS.md)。
- 本项目以 [MIT 许可证](LICENSE) 开源。
