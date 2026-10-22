# 研读助手 Reading Agent

现代软件开发方法实验 1「Bring Your Own Agent」。Agent 根据本地资料的实际内容回答问题、规划学习主题，使用标准 Function Calling 连接模型与文件工具。

仓库：https://github.com/zeroeclipse00/Experiment1-Bring-Your-Own-Agent

## 启动与真实模型

需要 Python 3.11+，在项目目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
Copy-Item .env.example .env
powershell -ExecutionPolicy Bypass -File .\start.ps1
```

仅在 `.env` 尚不存在时复制示例文件，避免覆盖自己的配置。真实模式需要填写 `AGENT_MODE=live`、`LLM_API_KEY`、`LLM_MODEL`、`LLM_BASE_URL`。地址填写 API 前缀，程序追加 `/chat/completions`。服务需要支持标准工具调用及严格 JSON Schema。

打开 <http://127.0.0.1:8000>。修改 `.env` 或 Python 代码后，在原服务终端按 Ctrl+C，再执行启动命令。刷新网页不会重新加载环境配置。日期与时间使用北京时间（UTC+8）。

规则演示仅展示实际检索、读取和 CSV 统计，明确不调用 LLM，不生成伪装为内容推理的学习计划。三个快捷问题仅在规则演示下显示；切换模式不会改写输入框。真实模式由模型自主理解内容并调用规划工具。

## 资料输入与计划输出

所有学习资料统一放在 `data/library/`，支持 `.md`、`.txt`、`.pdf`、`.csv` 和子目录。新增文件后刷新网页，下一次请求读取最新文件。**没有必须填写的任务 CSV，也没有两份任务表的优先级或回退逻辑。**

CSV 是普通真实数据，例如实验测量、统计观测、课程数据集；不需要 `due_date`、`priority` 或 `estimated_minutes` 等计划字段。`analyze_csv` 自动分析列类型、缺失情况、数值统计、类别分布和样例。数据来源、单位等说明可以作为同目录的 TXT 资料。

最终计划由模型根据原文生成完整学习单元，作为 `create_study_plan` 的输入，经工具校验后形成输出。网页展示回答与实际工具记录，导出 JSON 包含结构化 `study_plan`，二者与输入 CSV 是不同的数据。

## 如何考虑内容、完整性和难度

1. 先检索资料并查看结构，再读取定义、定理、例题等原文，或统计分析真实 CSV。
2. 模型按知识主题提炼学习单元，为每个单元填写学习目标、来源、先修单元、难度及其依据、建议用时。不得按页数等比例分配。
3. 工具校验引用确实读取过、先修关系无循环、每日时间不超预算。完整单元放不进剩余时间时留到下一天，不在定义、证明或例题中间任意截断。单元超过整天预算时报告原因，由模型按自然学习目标细分后重排。
4. 整份资料规划使用 `whole_material`，要求原文或数据分析和学习单元的来源范围覆盖全文。只选部分主题使用 `selected_topics`，明确返回尚未覆盖的部分。
5. 难度、目标及用时是模型基于内容作出的学习建议；工具能验证来源、结构和预算，不能保证模型的教学判断绝对正确。未提供背景时按首次系统学习估算。

例如，若原文包含概念、性质、证明与应用，计划应说明这些主题之间的关系，并对较难的推导和练习留出时间，而不是自动划成“第1–8页、第9–16页”。页码只用于定位和检查覆盖。

## 五个工具与标准协议

| 工具 | 实际作用 |
| --- | --- |
| `search_materials` | 词法检索本地资料，返回命中片段与定位 |
| `inspect_material` | 查看页数、标题线索和读取覆盖；不冒充已阅读 |
| `read_material` | 分批读取原文，保存真实页码或行号证据 |
| `analyze_csv` | 分析任意列结构的真实数据，不读取预制任务表 |
| `create_study_plan` | 校验内容单元、引用、完整覆盖、先修关系与时间预算 |

系统提示词在 `agent/prompts.py`，工具和参数模型在 `agent/tools.py`、`agent/study.py`，编排循环在 `agent/runner.py`。样板、参数模型、请求适配、网页及测试使用 Codex 辅助生成。

协议流程：JSON Schema → 模型 `tool_calls` → 参数校验和工具执行 → `role=tool` + `tool_call_id` 回传 → 模型继续阅读或回答。采用 [标准 Function Calling](https://developers.openai.com/api/docs/guides/function-calling)，不额外运行 MCP Server，符合题目允许的 MCP or similar。

## 读取边界与资料质量

最多扫描80份文件；单文件最多5 MB，PDF最多100页。一次读取最多80行或3页、最多12000字符。CSV分析最多2 MB、10000行、30列。每次请求最多24轮模型编排、64次工具调用，可以在一轮中读取多个片段。每次请求独立，不长期记忆之前读过的内容。

31页文字型教材在页数限制内；整本学习计划需要按批读取全文，而不是一次把所有文件交给模型。能否处理还取决于文件编码、模型的上下文窗口和文字总量。没有自动上下文压缩；超大资料应分章处理并声明范围。

扫描件、空文本、检测到乱码或异常字体编码的 PDF 会明确报错，不能仅凭文件名推断正文。需要先 OCR 或换用可正常提取文字的版本。`inspect_material` 的缩略信息不计作正文阅读证据。

## CLI 与测试

```powershell
.\.venv\Scripts\python.exe -m agent "分析本地 CSV 的数据列和缺失值" --mode demo
.\.venv\Scripts\python.exe -m agent "阅读选定教材，按完整主题和难度制定3天学习计划，每天60分钟" --mode live --output output/live-plan.json
.\.venv\Scripts\python.exe -m pytest -q
```

测试使用独立样例目录，不读取个人教材。覆盖真实数据列分析、文件更新、参数与路径边界、PDF乱码、31页读取覆盖、内容来源、主题完整性、先修关系、预算、标准协议往返和无规划工具结果时的失败处理。

## 本地材料与隐私

公开仓库只保存源码、公开演示笔记、测试和部署说明。`.env`、`output/`、`docs/`、PDF、Word、`library` 内 CSV 及来源旁注默认忽略。报告、个人教材、数据和运行记录留在本地。真实模式会将问题及选定工具读取的资料发送到自己配置的模型服务；密钥仅用于后端鉴权。

网页默认绑定本机回环地址，不含公网用户认证。

## Docker

```powershell
docker build -t reading-agent .
docker run --rm --name reading-agent -p 127.0.0.1:8000:8000 --env-file .env reading-agent
```

镜像不复制 `.env`。个人数据可通过只读卷挂载到 `/app/data/library`；输入是学习资料，计划结果通过网页或 CLI 导出。
