# 研读助手 Reading Agent

GitHub 仓库：https://github.com/zeroeclipse00/Experiment1-Bring-Your-Own-Agent

现代软件开发方法实验 1「Bring Your Own Agent」。这是一个只负责**理解本地课程资料、安排阅读任务**的单用途 Agent：先用工具读取当前资料和 CSV，再回答问题或给出每日阅读安排。

## 对应实验要求

| 要求 | 本项目实现 | 关键文件 |
| --- | --- | --- |
| 至少两项独立技能 | 本地资料检索；Markdown/TXT/PDF 原文解析；CSV 任务分析与排期，共三项工具 | `agent/tools.py` |
| MCP 或同类标准协议 | 标准 LLM Function Calling：JSON Schema → `tool_calls` → 参数校验 → `role=tool` + `tool_call_id` → 模型继续决策 | `agent/brain.py`、`agent/runner.py` |
| 依赖外部工具与上下文 | 实时读取 `data/library/` 与 `data/reading_tasks.csv`，工具轨迹可展开、导出 | `data/`、网页执行记录 |
| AI 生成样板代码 | Codex 生成 Pydantic 模型、Schema、API 适配、工具实现、网页和测试；系统提示词单独维护 | `agent/tools.py`、`agent/brain.py`、`agent/prompts.py` |
| 可运行与部署 | 网页、CLI、Windows 启动脚本、Dockerfile | `start.ps1`、`Dockerfile` |

本实验采用 Function Calling，**没有实现或运行 MCP Server**；原题明确允许 MCP or similar / standard LLM function calling。协议依据：[OpenAI 官方 Function Calling 文档](https://developers.openai.com/api/docs/guides/function-calling)。

## 快速运行

需要 Python 3.11+，已在 Windows / Python 3.12 上验证。以下命令在 `homework1` 目录执行。

从 GitHub 获取代码：

```powershell
git clone https://github.com/zeroeclipse00/Experiment1-Bring-Your-Own-Agent.git homework1
cd homework1
```

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m uvicorn agent.app:app --host 127.0.0.1 --port 8000
```

打开 <http://127.0.0.1:8000>。或者执行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

`start.ps1` 在首次运行时创建虚拟环境并安装依赖；不修改系统执行策略。若系统默认 Python 不可用，可用 `py -3.12 -m venv .venv`。

修改 `.env` 或 Python 源码后，在运行服务的终端按 `Ctrl+C` 停止，再执行同一条启动命令即可重启。服务启动时读取 `.env`，仅刷新网页不会重新加载配置。没有服务运行时直接启动；启动后刷新网页，确认选择“真实模型”。

macOS / Linux 使用 `python3 -m venv .venv`，将上述 `.\.venv\Scripts\python.exe` 换为 `.venv/bin/python`。

### 两种模式

- **规则演示（默认）**：无需密钥，真实读取文件、运行所有工具并形成摘录和排期。工具选择由确定性规则驱动，**不含 LLM，不作为真实模型自主调用的证明**。支持阿拉伯数字形式的“未来3天”“每天60分钟”和 `YYYY-MM-DD` 起始日期；未提供时用北京时间的当前日期、3 天、每天 60 分钟。
- **真实模型**：LLM 自主理解问题、选择工具、读取上下文并总结。需要提供支持 Chat Completions Function Calling 与严格 JSON Schema 的模型服务。

### 连接真实模型

```powershell
Copy-Item .env.example .env
```

编辑 `.env`，不要将密钥提交到 Git：

```dotenv
AGENT_MODE=live
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=填写你自己的密钥
LLM_MODEL=填写该服务支持工具调用的模型名
LLM_TIMEOUT=60
```

`LLM_BASE_URL` 是 API 前缀，程序会追加 `/chat/completions`，不要填到接口末尾。其他 OpenAI-compatible 服务也可配置，但必须实际支持 `tools`、`tool_calls`、`strict` 与 `tool_call_id`。模型名由使用者填写，不预设账户一定可用的模型。

重启服务，在网页选“真实模型”。**不能用演示记录替代课程中的真实 LLM 演示**：提交前请配置真实 API，完成至少一次含工具调用的运行，并导出 JSON 作为证据。没有配置时程序会明确报错，不会自动回退为演示。

真实模型模式会将问题及工具读取的资料发送至配置的模型服务；密钥仅用于后端鉴权，不下发浏览器或写入运行记录。

## 使用示例

网页支持示例问题、执行轨迹展开和 JSON 导出；每次运行是独立任务，不保存多轮聊天历史。

```text
根据本地资料，说明 Agent 的工具调用流程。
比较资料中 MCP 与 Function Calling 的上下文接入方式。
从2026-10-08开始，安排未来3天的阅读计划，每天60分钟。
```

第三个示例固定日期，便于复现：未完成任务共 **215 分钟**，3 天容量 **180 分钟**，未排完 **35 分钟**。已完成的 R05 不参与排期。到其他日期展示时，建议更新 CSV 截止日期或明确输入计划起始日期。

CLI 同样使用相同编排循环：

```powershell
.\.venv\Scripts\python.exe -m agent "阅读 Agent 资料，从2026-10-08开始安排未来3天计划，每天60分钟" --mode demo --output output/demo-run.json
.\.venv\Scripts\python.exe -m agent "根据资料解释 Function Calling 的流程" --mode live --output output/live-run.json
```

退出码 0 表示成功，1 表示任务未完成。JSON 包含模式、模型、问题、回答、调用参数、实际结果、轮次和耗时。

## 替换自己的上下文

将 UTF-8 编码的 `.md`、`.txt` 或文字型 `.pdf` 放入 `data/library/`，可使用子目录；刷新页面更新文件清单，下一次运行读取最新内容。项目资料是自编教学示例，**不是课程官方讲义或真实论文**。

编辑 `data/reading_tasks.csv`：

| 字段 | 含义 |
| --- | --- |
| `id` | 唯一、非空任务编号 |
| `title` | 非空任务标题 |
| `material` | 对应资料文件名；作为元数据，不意味着已阅读 |
| `due_date` | `YYYY-MM-DD` 截止日期 |
| `estimated_minutes` | 剩余分钟数，整数 1–10000 |
| `priority` | 1–3，数字越小越优先 |
| `status` | `todo`、`doing`、`done`；`done` 不安排 |

排期按截止日期、优先级、id 排序，可跨天拆分，单日不超过预算；标记逾期，列出尚未安排时间。`doing` 也按 CSV 的分钟数作为剩余量，不推测进度。

检索采用英文单词与中文双字匹配，适用于小型课程资料库；不是向量 RAG。最多扫描 80 份合规文件、单文件 5 MB、PDF 100 页；一次最多读取 80 行或 3 页、返回最多 12000 字符。大型 PDF 应拆分；扫描 PDF 需自行先 OCR。

例如 31 页的文字型教材，只要不超过 5 MB，便在文件读取限制内。针对具体问题可检索并分批阅读相关页；这不代表把整本书一次送入模型。全文是否能放进上下文，取决于实际文字量、模型窗口和输出预留。当前最多 8 轮编排，逐次读取整本书还可能达到轮次上限，整本总结建议按章节分次处理。

## 架构与核心提示词

```text
网页 / CLI
    ↓ 用户目标
AgentRunner + System Prompt + 当前北京时间（UTC+8）
    ↓ messages + tools JSON Schema
LLM（真实模式） / 确定性驱动器（规则演示）
    ↓ tool_calls(name, arguments, id)
Pydantic 校验 → 白名单工具注册表 → 本地 Markdown / TXT / PDF / CSV
    ↓ role=tool, tool_call_id, JSON 结果
回到模型继续决策 → 最终回答 + 资料引用 + 可导出执行记录
```

核心提示词完整存于 `agent/prompts.py`，强调：先调用工具再回答、先检索再阅读、引用原文定位、区分事实与建议、忽略资料内的提示注入、遇到缺失上下文明确说明。工具失败会作为结构化结果回传，供模型修正参数。模型最多编排 8 轮、执行 24 次工具调用；至少一次工具成功才接受最终回答。

路径按解析后的绝对位置检查，限制在 `data/library`；工具不执行任意 shell 或访问任意 URL。提示词约束不是严格的事实正确性保证：真实模型仍可能错引或漏引，展示时可展开原文核对。服务设计用于个人本机，默认绑定回环地址，不含公网用户认证。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖参数校验、路径越界、PDF 读取、扫描件反馈、CSV 格式、每日预算、逾期、外部数据变化、演示端到端、标准 Function Calling HTTP 消息往返、错误恢复与循环上限。模拟 HTTP 服务验证协议，不代表真实 LLM 已联网验证。

仓库仅包含可运行源码、公开示例数据、测试与部署说明。`docs/`、`output/`、PDF、Word 文档以及环境密钥文件已加入忽略规则，运行记录和个人课程材料请保存在本地。

## Docker 部署

```powershell
Copy-Item .env.example .env
docker build -t homework1-reading-agent .
docker run --rm --name reading-agent -p 127.0.0.1:8000:8000 --env-file .env homework1-reading-agent
```

打开 <http://127.0.0.1:8000>。Docker 镜像不复制 `.env`，启动时注入环境变量。镜像默认包含项目样例资料；更新资料后重建镜像，或挂载自己的 `data` 目录。

## 常见问题

- **代理连接失败**：先检查本机代理是否启动。若网络允许直连，可在当前 PowerShell 窗口设置 `$env:NO_PROXY='*'` 后重试安装；这只对当前进程环境生效。不要把网络或代理凭据写进代码。
- **端口被占用**：用 `start.ps1 -Port 8001`，或修改 uvicorn 的 `--port`。
- **401 / 403 / 429 / 404**：检查 API 密钥、账户权限或额度、模型名和 API 前缀；程序不回显响应体中的敏感信息。
- **模型不调用工具**：确认服务真正支持 Function Calling；程序会要求获取上下文，达到轮次上限则报错。
- **找不到资料**：试用较短关键词；检查 UTF-8 编码、文件类型、大小及 PDF 是否有文字。
