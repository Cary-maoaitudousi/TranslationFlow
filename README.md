# TranslationFlow · 猫武士同人翻译流水线

把一个结构化的英文猫武士同人文档（.txt / .docx，可含上百章），按章自动跑成
**带章节序号的中文 Word 译文**；人名地名等专有名词按你自己的替换表统一译法，
翻译前后各有一道**人工审核**卡口，全程的 token 消耗与费用都记在账上。

- **全流程 11 步**：摘章 → 换名 → AI 找新名字 → **人工审核** → 二次换名 →
  AI 翻译 → AI 查"人类化表达" → **人工审核** → 润色替换 → AI 起四字章名 → 写 Word
- **所有 AI 调用统一走 DeepSeek**（OpenAI 兼容接口，`response_format=json_object` 强制 JSON）
- **确定性工作全部本地完成**：解析、替换、分块、拼装、写 Word、记账，不花一分 token
- **成本可见可控**：按峰谷分时计价、预算闸门、请求缓存、幂等跳过、实测约 **¥0.05/章**

> 本项目只处理你自己提供的文本。翻译结果由模型生成，发布前请自行校对并确认对原作的授权。

---

## 目录

| 章节 | 内容 |
|---|---|
| [1. 工作原理](#1-工作原理) | 11 步流程与每步产物 |
| [2. 环境与安装](#2-环境与安装) | Python 版本、依赖、目录约定 |
| [3. 第一次运行](#3-第一次运行) | 5 步跑通第一章 |
| [4. 主控界面](#4-主控界面) | 每个区域做什么 |
| [5. 审核窗口](#5-审核窗口) | 快捷键与审核语义 |
| [6. 命令行参考](#6-命令行参考) | 13 个子命令 |
| [7. 配置项全表](#7-配置项全表) | 每个键的默认值与含义 |
| [8. 替换表维护](#8-替换表维护) | TXT/JSON 导入、编辑器、体检 |
| [9. 文风样本](#9-文风样本) | 如何提供、如何被截取 |
| [10. 提示词与 JSON 契约](#10-提示词与-json-契约) | 四个 AI 步骤的输入输出 |
| [11. 成本控制](#11-成本控制) | 峰谷计价、实测数据、省钱的每一层 |
| [12. 运行产物](#12-运行产物) | 正常跑完到底留下什么 |
| [13. 日志与排错](#13-日志与排错) | 日志文件、错误分级、FAQ |
| [14. 测试](#14-测试) | 136 项测试覆盖什么 |
| [15. 打包与分发](#15-打包与分发) | 源码包 / 独立 exe |
| [16. 项目结构](#16-项目结构) | 文件树 |
| [17. 已知限制](#17-已知限制) | 边界与注意事项 |

---

## 1. 工作原理

```
                    ┌────────────────────── 每章循环 ──────────────────────┐
英文文档 ──①摘章──▶ raw.txt
                    │
                    ├─②模式A换名（全局表）──▶ replaced.txt
                    │
                    ├─③AI 找未翻译的专有名词（JSON）──▶ new_nouns.json
                    │
                    ├─④【人工审核】通过/改译名/跳过 ──▶ 追加进全局表
                    │
                    ├─⑤以 raw.txt 为基准再换一次名 ──▶ final_mixed.txt（中英混合）
                    │
                    ├─⑥AI 翻译（按段落分块，附文风样本）──▶ translated.txt（纯中文）
                    │
                    ├─⑦AI 找"人类化表达"（短语级）──▶ polish.json
                    │
                    ├─⑧【人工审核】预览替换效果 ──▶ polish_approved.json（按章临时）
                    │
                    ├─⑨模式B润色替换 ──▶ final_chinese.txt
                    │
                    ├─⑩AI 起 5 个四字章名（JSON）──▶ titles.json
                    │
                    └─⑪写 Word（第 N 章 章名 + 分段 + 分页）──▶ output/译文.docx
```

| 步骤 | 名称 | 谁来做 | 消耗 token | 产物 |
|---|---|---|---|---|
| 1 | 章节摘取 | 本地 | 否 | `chapter_{n}_raw.txt`、`chapter_{n}_meta.json` |
| 2 | 专有名词初步替换（模式 A） | 本地 | 否 | `chapter_{n}_replaced.txt` |
| 3 | AI 识别未翻译专有名词 | FLASH（可改） | 是 | `chapter_{n}_new_nouns.json` |
| 4 | **人工审核**专有名词 | 你 | 否 | `chapter_{n}_approved_nouns.json` + 追加全局表 |
| 5 | 二次专有名词替换（**基准 = raw**） | 本地 | 否 | `chapter_{n}_final_mixed.txt` |
| 6 | AI 翻译（分块 + 文风样本） | PRO（可改） | 是 | `chapter_{n}_translated.txt` |
| 7 | AI 检测文化不适配表达 | PRO（可改） | 是 | `chapter_{n}_polish.json` |
| 8 | **人工审核**润色表 | 你 | 否 | `chapter_{n}_polish_approved.json` |
| 9 | 润色替换（模式 B） | 本地 | 否 | `chapter_{n}_final_chinese.txt` |
| 10 | AI 推荐四字章名 | FLASH（可改） | 是 | `chapter_{n}_titles.json` |
| 11 | 写入 Word | 本地 | 否 | `output/译文.docx`（追加） |

**为什么步骤 5 要以 `raw.txt` 为基准**：如果拿步骤 2 的输出再替换一次，连锁替换会二次污染
（A→B 之后 B 又被 B→C 命中）。所以每次都从原文重跑一遍完整替换表。

**"章"怎么数**：`START_CHAPTER` 指的是"文档里第几个章头"（1 基），不是章号。
Word 里写的是章头里的数字（`Chapter 100` → `第 100 章`），可用 `CHAPTER_LABEL_MODE=loop`
改成"本次循环的第几章"。

**章头识别**：默认正则 `^\s*Chapter\s+(\d+)`，并额外要求章号后面紧跟冒号/换行/分隔符，
所以章末 Notes 里那种 `Chapter 100 baybee!!` 不会被误判成新章。其它格式用
`CHAPTER_HEADING_PATTERN` 覆盖。正文会自动去掉站点常见的行首缩进。

---

## 2. 环境与安装

### 要求

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（主控界面用 PySide6；日志"双击打开"用了 `os.startfile`，其它系统会退化为提示） |
| Python | 3.10 及以上（本机验证版本 3.11.9 / 3.12.9） |
| 网络 | 能访问 `https://api.deepseek.com` |
| 磁盘 | 源码 + 依赖约 400MB（PySide6 占大头）；缓存与日志通常 < 50MB |

### 安装

```powershell
# 1) 建虚拟环境（本仓库开发时用的是 .venv311，可任意命名）
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt`：

```
openai>=1.40.0          # DeepSeek 走 OpenAI 兼容接口
python-docx>=1.1.0      # 读 .docx 源文档、写 .docx 译文
python-dotenv>=1.0.1    # 读 .env
PySide6-Essentials>=6.6.0   # 主控界面与三个审核窗口
pytest>=8.0.0           # 测试
```

### 密钥放在哪里（三选一，都不会进 git）

1. **主控界面**：填 API Key → 点「保存密钥」→ 写入 `.env`（已列入 `.gitignore`）；
2. 手动：`Copy-Item .env.example .env`，然后填 `DEEPSEEK_API_KEY=`；
3. 临时：`$env:DEEPSEEK_API_KEY='sk-...'`（只对当前终端有效）。

Base URL 默认 `https://api.deepseek.com`，走中转网关时改 `DEEPSEEK_BASE_URL`。
**密钥只在环境变量/`.env` 里读，永远不会写进 `config.json`，也不会出现在日志里。**

---

## 3. 第一次运行

```powershell
# ① 看一眼源文档解析结果（不花钱）
python cli.py chapters -c config.json
#    输出每章：序号 / 章头 / 段数 / 词数 / 是否含章末 Notes

# ② 估价（不花钱）
python cli.py estimate -c config.json --chapter 1
#    输出：体量画像 + 分块计划 + 各步预估费用（按当前峰谷档位）

# ③ 零成本跑通全流程（MOCK 模式，不联网）
python cli.py run -c config.json --mock --auto-approve --start 1 --count 1 -y
#    检查 output/译文.docx 是否生成、结构是否正常

# ④ 打开界面正式跑
python main.py
#    起始章=1，循环章数=1，取消勾选「MOCK」，点「开始」
```

跑完检查：

| 位置 | 内容 |
|---|---|
| `output/译文.docx` | 第 1 章标题 + 正文分段 + 章末分页 |
| `data/noun_table.json` | 审核通过的新专名已合并进去 |
| `logs/cost_log.csv` | 本次调用的 token 与费用 |
| `logs/progress.json` | 第 1 章已完成（下次再跑会跳过） |

---

## 4. 主控界面

```
┌─ 输入输出 ────────────────────────────────────────────┐
│ 源文档（.txt/.docx）  [浏览…]                          │
│ 输出 Word             [浏览…]                          │
│ 全局专有名词表        [浏览…]                          │
│ 替换表   [导入替换表(TXT/JSON)…] [编辑替换表…]          │
│ 文风样本              [浏览…]                          │
├─ 循环与预算 ──────────────────────────────────────────┤
│ 循环  起始章 [1] 章数 [1] 章号口径 [doc▾] 章名 [auto▾]  │
│ 成本  总预算 [10] 单章预算 [0.3] 分块 token [6000]     │
│ 开关  □MOCK □审核全自动 ☑跳过已完成 ☑成本优先 □等空闲   │
│ 操作  [保存配置][校验配置][预估成本][列出章节]          │
│ 法定节假日 [2026-01-01, …] [怎么填？]                  │
├─ 每一步的模型与思考模式 ──────────────────────────────┤
│ 步骤3 人名识别   [Flash▾] □开启                        │
│ 步骤6 翻译       [Pro▾]   □开启                        │
│ 步骤7 润色检测   [Pro▾]   □开启                        │
│ 步骤10 章名      [Flash▾] □开启                        │
├─ DeepSeek API ────────────────────────────────────────┤
│ API Key [••••] [保存密钥][测试连接]                    │
│ Base URL [https://api.deepseek.com]  □保存到 .env      │
├─ 步骤清单 │ 运行日志 / 成本 / 错误（双击打开日志）────┤
└─ [开始][暂停][继续][停止]  状态：…   [进度条] ─────────┘
```

| 区域 | 说明 |
|---|---|
| **输入输出** | 四个路径都带浏览按钮；**替换表**这一行就是导入与编辑入口 |
| **循环与预算** | 起始章/章数决定跑哪几章；`章号口径` doc=用原文数字，loop=用循环序；总预算/单章预算填 `0` 表示不限；分块 token 越小越省但调用次数多 |
| **开关** | `MOCK` 不联网不花钱；`审核全自动` 跳过三个人工确认；`跳过已完成` 复用进度记录；`成本优先` 按峰谷计价；`等空闲` 高峰时真的等到空闲时段再开跑 |
| **法定节假日** | 只影响计价（填了的日期按空闲价），格式 `YYYY-MM-DD`，逗号分隔 |
| **每一步的模型与思考** | 四步各自选 Flash/Pro、是否开思考；点「预估成本」按当前组合报价 |
| **DeepSeek API** | 填 Key → 保存/测试；测试连接会发一次最小请求（约 ¥0.0003） |
| **底部** | 开始/暂停/继续/停止 + 11 步进度 + 进度条，**永远可见不随配置滚动** |
| **错误区** | 按章按步骤列出，**双击打开对应模块的日志**（doc_parser / replacer / deepseek / review / word） |

**布局行为**：配置区放进滚动区，窗口压小时出现滚动条；配置区与日志区之间、
日志与错误区之间都有可拖动分隔条。

---

## 5. 审核窗口

三个窗口共用同一套键盘操作：

| 按键 | 行为 |
|---|---|
| `Space` | 通过当前条并跳到下一条（再按一次＝取消通过） |
| `Enter` | 进入编辑；再按 `Enter` 保存并跳下一条 |
| `←` / `→` | 上一条 / 下一条 |
| `F6` | 确认提交（**独立于 Enter**，防止惯性误操作） |
| `Esc` | 取消（本章终止） |

### 5.1 专有名词审核（步骤 4）

- 上表：`原文 | AI 推荐译名 | 状态`，译名可直接改；
- 下框：**全局表 + 本次通过条目**合并后的完整替换表，边审边看全貌；
- 译名为空的条目按 `Space` 会被拒绝通过；
- 按钮：`确认提交(F6)`、`跳过本章新增`（不写表）、`取消`。

### 5.2 润色表审核（步骤 8）

- 上表：`原文片段 | 替换为 | 说明 | 状态`；
- 下框：**替换后受影响段落的 原文→替换 对照预览**（内存中跑一次模式 B，不落盘）；
- `source` 在译文中找不到的条目标为「待人工确认」，不阻断审核；
- 润色表**按章临时**，不跨章累积。

### 5.3 章名选择（步骤 10，`TITLE_MODE=manual` 时）

5 个候选（风格 / 四字标题 / 一句理由），双击或 `Enter` 选定。

### 5.4 不花钱先看效果

```powershell
python cli.py review-demo -c config.json                 # 三个窗口依次弹出
python cli.py review-demo -c config.json --capture out   # 渲染成 PNG
```

演示数据来自你真实的替换表与文风样本，并故意埋了「空译名」「译文中不存在的 source」两种坑。

---

## 6. 命令行参考

`python cli.py <命令> [参数]`，`-c/--config` 可写在子命令前或后。

| 命令 | 作用 | 关键参数 |
|---|---|---|
| `chapters` | 列出源文档各章 | `--start/--count` 无实际作用 |
| `estimate` | 单章成本预估（不调 API） | `--chapter N` |
| `run` | 执行流水线 | `-y` 跳过付费确认；`--mock`；`--auto-approve`；`--start N`；`--count N`；`--no-skip`；`--model-pro/--model-flash` |
| `report` | 累计成本（按章/按步） | — |
| `pricing` | 价目表、当前档位、下一个空闲时段 | — |
| `prompts` | 打印提示词 | `--name nouns/translate/polish/titles/all` |
| `import-nouns` | 导入 `英文->中文` 表 | `--txt 表.txt`、`--replace` |
| `export-nouns` | 导出为 TXT | `--txt 备份.txt` |
| `audit-nouns` | 替换表命中分布与误伤提示 | `--chapters 5` |
| `review-demo` | 打开/渲染审核窗口 | `--capture DIR` |
| `holidays` | 查看/设置法定节假日 | `--set "2026-01-01,2026-02-17"` |
| `cache` | 查看/清空请求缓存 | `--clear` |
| `init` | 生成 `config.json` 与目录骨架 | `--source 文档.txt` |

常用组合：

```powershell
# 零成本回归
python cli.py run -c config.json --mock --auto-approve --start 1 --count 3 -y
# 真跑 5 章，人工审三次窗口
python cli.py run -c config.json --start 6 --count 5
# 全量 131 章（约 ¥6~9，70~110 分钟）
python cli.py run -c config.json --start 1 --count 131
# 强制按空闲价对账
$env:PRICING_TIER='offpeak'; python cli.py run -c config.json --start 1 --count 1 -y
```

> 所有 CLI 参数都能用同名环境变量覆盖配置，例如 `$env:LOOP_COUNT='5'`。

### 工具脚本（`tools/`）

| 脚本 | 用途 |
|---|---|
| `show_cost.py 路径/cost_log.csv` | 明细表 + 按步骤汇总 |
| `show_polish.py work/runX N` | 某章的润色候选与改动 |
| `compare_runs.py run1 run2` | 两次运行的译文/润色/章名/成本对比 |
| `inspect_docx.py 译文.docx` | 检查标题、段落数、分页符 |
| `total_spend.py work` | 汇总目录下所有账单 |
| `footprint_check.py before/after` | 验证一次运行到底新增了哪些文件 |
| `gui_shot.py out.png [--table]` | 渲染界面截图（不跑流水线） |
| `probe_api.py` | API 连通性自检（两份最小请求，约 ¥0.003） |
| `probe_step7.py work/runX N` | 单独验证润色步骤（会注入人类化句子） |
| `probe_polish_raw.py 文件 [phrase/sentence]` | 打印润色的原始返回，排查模型乱输出 |

---

## 7. 配置项全表

优先级：**环境变量 > `config.json` > 代码默认值**。下表是代码默认值；
本仓库附带的 `config.json` 已按实测最优填好。

### 7.1 路径与循环

| 键 | 默认 | 说明 |
|---|---|---|
| `SOURCE_DOC_PATH` | `""` | 源文档，支持 `.txt` / `.docx` / `.md` |
| `OUTPUT_DOC_PATH` | `output/译文.docx` | 输出 Word，不存在则新建，存在则追加 |
| `GLOBAL_NOUN_TABLE_PATH` | `data/noun_table.json` | 全局替换表（跨章累积） |
| `STYLE_SAMPLE_PATH` | `data/style_sample.txt` | 文风样本（翻译时的 few-shot 参考） |
| `START_CHAPTER` | `1` | 从文档第几个章头开始（1 基） |
| `LOOP_COUNT` | `1` | 本次处理章数 |
| `CHAPTER_HEADING_PATTERN` | `^\s*Chapter\s+(\d+)` | 章头正则，必须含一个捕获组 |
| `CHAPTER_LABEL_MODE` | `doc` | `doc`=Word 用原文章号；`loop`=用循环序 |

### 7.2 模型与思考模式

| 键 | 默认 | 说明 |
|---|---|---|
| `MODEL_FLASH` | `deepseek-flash` | 经济型模型名 |
| `MODEL_PRO` | `deepseek-v4-pro` | 高性能模型名 |
| `MODEL_STEP3` | `""` | 步骤 3 用哪个模型（空=FLASH） |
| `MODEL_STEP6` | `""` | 步骤 6（空=PRO） |
| `MODEL_STEP7` | `""` | 步骤 7（空=`MODEL_POLISH`→PRO） |
| `MODEL_STEP10` | `""` | 步骤 10（空=FLASH） |
| `THINKING_STEPS` | `[]` | 开思考模式的步骤号。**实测开与关差 5 倍价格**，默认全关 |
| `REASONING_EFFORT` | `high` | 思考强度（`high`/`medium`/`low`，仅思考模式生效） |

### 7.3 重试与超时

| 键 | 默认 | 说明 |
|---|---|---|
| `MAX_RETRIES` | `5` | 429/concurrency 的最大重试次数 |
| `RETRY_BACKOFF_BASE` | `1.0` | 指数退避基数（秒） |
| `RETRY_BACKOFF_MAX` | `60.0` | 单次退避上限（秒） |
| `REQUEST_TIMEOUT_SEC` | `600` | 单次请求超时；长章翻译建议 ≥300 |

### 7.4 分块与输出额度（动态）

| 键 | 默认 | 说明 |
|---|---|---|
| `MAX_INPUT_TOKENS_PER_CHUNK` | `6000` | 每块输入预算；调小=更省单次额度但调用次数多 |
| `MAX_STYLE_SAMPLE_TOKENS` | `2000` | 文风样本预算，超出则摘取代表性段落 |
| `CHARS_PER_TOKEN_EN` | `4.0` | 英文 token 估算系数（4 字符≈1 token） |
| `TITLE_INPUT_CHAR_LIMIT` | `6000` | 起章名时只看译文首尾这么多字符 |
| `OUTPUT_TOKENS_PER_INPUT` | `1.7` | 输出额度 = 输入 token × 该系数 |
| `MIN_CHUNK_OUTPUT_TOKENS` | `1200` | 单块输出下限 |
| `MAX_OUTPUT_TOKENS_CAP` | `8000` | 单块输出上限 |
| `THINKING_TOKEN_HEADROOM` | `2500` | 开思考时额外预留的推理额度（否则会被截断） |
| `MAX_TOKENS_HARD_CAP` | `16000` | 截断重试时允许涨到的硬上限 |
| `TRUNCATION_RETRIES` | `2` | `finish_reason=length` 时的扩容重试次数 |

### 7.5 润色步骤

| 键 | 默认 | 说明 |
|---|---|---|
| `MAX_POLISH_ENTRIES` | `15` | 单章润色候选上限，防止模型刷上百条 |
| `POLISH_OUTPUT_MODE` | `phrase` | `phrase`=只报短语（省 token，推荐）；`sentence`=报整句 |
| `POLISH_CONTEXT_WINDOW` | `14` | 短语模式下本地展开上下文的半宽（字） |

### 7.6 成本与峰谷

| 键 | 默认 | 说明 |
|---|---|---|
| `COST_PRIORITY` | `false` | 成本优先：按峰谷分时计价（计价始终按真实档位，此项只影响提示） |
| `WAIT_FOR_OFFPEAK` | `false` | 高峰时**真的等到**空闲时段再开跑（可暂停/停止打断） |
| `PRICING_TIER` | `auto` | `auto` 看当前时间；也可强制 `peak`/`offpeak` 对账 |
| `PEAK_WINDOWS` | `["09:00-12:00","14:00-18:00"]` | 高峰时段（北京时间） |
| `PEAK_WEEKDAYS` | `[0,1,2,3,4]` | 高峰星期（0=周一），周末全天空闲 |
| `HOLIDAYS` | `[]` | 法定节假日 `YYYY-MM-DD`，全天按空闲价 |
| `BUDGET_TOTAL_CNY` | `0` | 总预算，0=不限 |
| `BUDGET_PER_CHAPTER_CNY` | `0` | 单章预算，0=不限 |
| `CACHE_ENABLED` | `true` | 相同请求命中本地缓存 → 0 花费 |
| `CACHE_DIR` | `.cache/deepseek` | 缓存目录 |
| `PRICE_TABLE` | 见下 | 价目表，峰谷两档 |

### 7.7 幂等与产物

| 键 | 默认 | 说明 |
|---|---|---|
| `SKIP_COMPLETED` | `true` | 已完成章节直接跳过（不产生附件、不重复写 Word） |
| `CLEAN_INTERMEDIATES` | `true` | 成功后删除该章中间产物；失败时保留现场 |
| `KEEP_CACHE` | `true` | 是否保留请求缓存 |
| `WORK_DIR` | `work` | 中间产物根目录（正常跑完为空） |
| `LOG_DIR` | `logs` | 日志目录 |
| `COST_LOG_PATH` | `logs/cost_log.csv` | 账单 CSV |
| `PROGRESS_PATH` | `logs/progress.json` | 已完成章节记录 |
| `LOG_LEVEL` | `INFO` | 文件日志级别 |

### 7.8 审核与输出

| 键 | 默认 | 说明 |
|---|---|---|
| `AUTO_APPROVE` | `false` | 全自动审核（跳过三个窗口，调试/批量用） |
| `TITLE_MODE` | `auto` | `auto`=取模型第一条；`manual`=弹窗选 |
| `WORD_TITLE_TEMPLATE` | `第 {label} 章  {title}` | 标题模板 |
| `WORD_FONT_NAME` | `""` | 空=沿用 Word 默认字体 |
| `WORD_FONT_SIZE` | `0` | 0=默认字号 |
| `WORD_LINE_SPACING` | `0` | 0=默认行距 |
| `MOCK_MODE` | `false` | 不联网，用本地桩返回（联调用） |

---

## 8. 替换表维护

### 8.1 两种格式，统一存成 JSON

**TXT**（`英文->中文`，一行一条）：

```
ThunderClan->雷族
ShadowClan->影族
# 井号开头是注释
Moonpool -> 月池
```

- 分隔符支持 `->`、`→`、`=>`、制表符；
- 编码自动探测 UTF-8 / UTF-8-BOM / GBK；
- 解析不了的行会被收集并在界面上提示。

**JSON**（统一存储格式）：

```json
{
  "version": 1,
  "entries": [
    { "source": "ThunderClan", "target": "雷族" }
  ]
}
```

也兼容裸数组 `[{...}]`。

### 8.2 三个入口

| 入口 | 说明 |
|---|---|
| 主控界面「导入替换表（TXT/JSON）」 | 选文件 → 合并或覆盖 → 直接写回 JSON |
| 主控界面「编辑替换表」 | 表格逐条改/加/删、搜索、导出 TXT、**合并大小写重复**、保存前校验 |
| `cli.py import-nouns --txt 表.txt [--replace]` | 命令行批量导入 |

### 8.3 替换规则（很重要）

- **区分大小写**（人名通常首字母大写）；
- **全词匹配**，用 ASCII 字母边界（`(?<![A-Za-z])…(?![A-Za-z])`），
  所以中英混排 `火星Firestar的尾巴` 也能正确命中；
- **长词优先**：`Thistleclaw` 不会被 `Thistle` 先吃掉；
- `target` 写成空字符串 = 删除匹配项；
- `kit`/`paw`/`moon`/`star` 这类**全小写通用后缀词**也会命中正文里的独立单词
  （`white paw` → `white 爪`）。用 `cli.py audit-nouns --chapters 5` 可以看到命中上下文，
  不想保留就从表里删掉——整名交给 AI 命名规则处理即可。
- 表里如果同时有 `ThunderClan` 和 `thunderClan`，两条都会生效（大小写不同）。
  编辑器里的「合并大小写重复」可以一键清理。

### 8.4 替换表体检

```powershell
python cli.py audit-nouns -c config.json --chapters 5
```

输出：命中最多的条目、从未命中的条目数、"疑似误伤"（通用词被当独立单词替换）及其上下文。
**不调用 API。**

---

## 9. 文风样本

`STYLE_SAMPLE_PATH` 指向一份**你已有的中文译文**（几千到几万字都行）。翻译时它会被放进
system prompt，要求模型学习"叙事节奏、用词偏好、句式特征"。

- 超过 `MAX_STYLE_SAMPLE_TOKENS`（默认 2000）时，会摘取**开头 / 对话 / 战斗 / 心理描写**
  四类代表性段落，并在提示词里标注"样本为节选"；
- 样本缺失不会中断流程（日志有 WARNING），但译文风格会明显漂移；
- 样本越长越贵：它会在**每次分块调用**里重复出现（DeepSeek 的上下文缓存会显著降低成本）。

---

## 10. 提示词与 JSON 契约

四个 AI 步骤都强制 `response_format={"type":"json_object"}`，提示词里同时出现
"json" 字样与具体样例。解析代码与提示词共用同一套 schema（`tf/prompts.py` 的
`JSON_SCHEMAS`）。

| 步骤 | 输入 | 输出 schema | 关键约束 |
|---|---|---|---|
| 3 人名识别 | 已初步换名的文本 | `{"entries":[{"source","target"}]}` | 保留 `xxxxkit→XX崽`、`xxxxpaw→XX爪`、`xxxxstar→XX星`、合成词灵活译；已是中文的不再提取 |
| 6 翻译 | 中英混合正文 + 文风样本 | `{"translation":"…","notes":["…"]}` | 不改动已给的中文专名；纯中文；保留分段；不加解释 |
| 7 润色检测 | 纯中文译文 | `{"entries":[{"phrase","replacement","context"}]}` | 只报人类化表达（肢体、"人"字、人类习语、不合适的量词）；只报真正要改的 |
| 10 章名 | 译文（首尾节选） | `{"titles":[{"style","title","reason"}]}` | 5 条、不同风格、**恰好四个汉字**、一句理由 |

**解析失败的处理顺序**（全部自动，不中断）：

1. 空 content（DeepSeek 可能返回全空格且 `finish_reason=stop`）→ 视为失败；
2. `temperature=0` 重问一次，并重申 JSON schema；
3. 仍失败 → 逐行找 `->` 做宽松解析（适合人名/润色这种"左右对照"结构）；
4. 还有认不出的行 → 写入 `chapter_{n}_manual_review.log`，继续跑。

**输出被截断**（`finish_reason=length`）：不是重问，而是**扩容 `max_tokens` 后重试**，
第二次仍截断就自动关掉思考模式再兜一次（思考的推理 token 也算在 `max_tokens` 里）。

---

## 11. 成本控制

### 11.1 峰谷计价（已按官方价目表校准）

| 元 / 百万 tokens | Flash 高峰 | Flash 空闲 | Pro 高峰 | Pro 空闲 |
|---|---|---|---|---|
| 输入（缓存命中） | 0.04 | 0.02 | 0.30 | 0.15 |
| 输入（缓存未命中） | 2.0 | 1.0 | 9.0 | 4.5 |
| 输出 | 8.0 | 4.0 | 27.0 | 13.5 |

高峰 = 北京时间周一至周五 9:00-12:00、14:00-18:00（不含法定节假日）；
其余时间（含周末、节假日全天）按空闲价，**恰好是高峰的一半**。
`python cli.py pricing` 显示当前档位与下一个空闲时段。

### 11.2 实测数据（131 章测试文档，单章 1300~2000 词）

| 配置 | 单章成本 | 单章耗时 | 131 章推算 |
|---|---|---|---|
| 思考开 + 句子级润色 | ¥0.54 | 386s | ≈ ¥70 |
| 思考关 + 句子级润色 | ¥0.28 | 147s | ≈ ¥37 |
| **思考关 + 短语级润色（默认）** | **¥0.046 ~ 0.066** | **32~50s** | **≈ ¥6 ~ 9** |

明细示例（第 3 章，总 ¥0.046 / 32s / 4 次调用）：

```
3-人名识别   flash  入 2189（命中 2048） 出  37   ¥0.0007
6-翻译       pro    入 2731（命中 2688） 出 1480  ¥0.0204
7-润色检测   pro    入 1964（命中    0） 出  513  ¥0.0187
10-章名推荐  flash  入 1931（命中  384） 出  266  ¥0.0062
```

**成本大头是输出 token**。思考模式的推理 token 也计入 `max_tokens`，一章能吃掉上万；
关掉后质量没有肉眼可见的下降。

### 11.3 省钱的每一层

| 手段 | 位置 | 效果 |
|---|---|---|
| 每步可选模型/思考 | 界面 / `MODEL_STEP*` | 只有翻译与润色用 Pro |
| 峰谷计价 + 等待 | `PRICING_TIER` / `WAIT_FOR_OFFPEAK` | 空闲价直接减半 |
| 预算闸门 | `BudgetGuard` | 每次调用前按估算预检，超了立刻停 |
| 请求缓存 | `.cache/deepseek` | 同章重跑 0 花费 |
| 幂等跳过 | `logs/progress.json` | 已完成章节连解析都省 |
| 动态输出额度 | `tf/sizing.py` | `max_tokens` 随块大小变，不打满 |
| 短语级润色 | `POLISH_OUTPUT_MODE=phrase` | 输出从 7000+ token 降到 ~500 |
| 空条目过滤 | `ai_steps` | 丢掉 `source==target` 的凑数条目 |
| 章名只看首尾 | `TITLE_INPUT_CHAR_LIMIT` | 省一次整章输入 |
| 文风样本节选 | `MAX_STYLE_SAMPLE_TOKENS` | 控制 system prompt 体积 |
| 全程记账 | `logs/cost_log.csv` | 每次调用（含重试）一行 |

**先估价再开跑**：`cli.py estimate`（命令行）或界面「预估成本」；真跑前还会再问一次。

---

## 12. 运行产物

### 12.1 正常跑完一章

| 路径 | 性质 |
|---|---|
| `output/译文.docx` | **唯一实质产物**（多章累积成一份） |
| `data/noun_table.json` | 你审核通过后新增的专有名词 |
| `logs/cost_log.csv` | 账单：token / 缓存命中 / 重试 / 费用 / 档位 |
| `logs/progress.json` | 已完成章节（用于跳过，避免重复写 Word） |
| `logs/*.log` | 六个模块的滚动日志（10MB × 5 备份） |
| `.cache/deepseek/` | 请求缓存（`cli.py cache --clear` 可清） |

**中间产物不会残留**：`CLEAN_INTERMEDIATES=true` 时章节成功后整个
`work/chapter_n/` 被删除；**失败时保留现场**（raw / translated / summary 都在），
这就是"只有报错才会出现一堆文件"的原因。调试时可改成 `false`。

验证方法：

```powershell
python tools/footprint_check.py before
python cli.py run -c config.json --mock --auto-approve --start 1 --count 1 -y
python tools/footprint_check.py after     # 列出前后新增的每一项
```

### 12.2 失败时保留什么

`work/chapter_n/` 下完整保留 11 步产物 + `chapter_n_summary.json`
（每步耗时/token/重试/降级标记/费用，审核统计，分块计划，峰谷档位）。

### 12.3 想清空重来

```powershell
Remove-Item -Recurse -Force work, output, .cache        # 产物/缓存
Remove-Item logs\progress.json                          # 忘记"已完成"
```

---

## 13. 日志与排错

### 13.1 日志文件

| 文件 | 内容 |
|---|---|
| `logs/main.log` | 流水线编排、成本、异常堆栈 |
| `logs/doc_parser.log` | 章头识别、正文范围、Notes 处理 |
| `logs/replacer.log` | 模式 A/B 的命中与未命中、标点提示 |
| `logs/deepseek.log` | 每次调用：模型/思考/输入(缓存命中)/输出/重试/finish/json_ok/档位/费用/耗时 |
| `logs/review.log` | 审核与替换表读写 |
| `logs/word.log` | Word 写入 |

错误分级：`ERROR`（密钥无效、参数非法、重试耗尽、Word 写入失败）、
`WARNING`（429/超时重试、JSON 降级、空 content、source 不存在、章名非四字、输出截断）、
`INFO`（每次成功调用、跳过已完成、去重跳过）。

### 13.2 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `DEEPSEEK_API_KEY 未设置` | 没有密钥 | 界面「保存密钥」或写 `.env` |
| `401` / `Authentication Fails` | 密钥错/过期 | 重新填 Key，「测试连接」验证 |
| 步骤 6 报 `译文为空` | 输出被截断或模型拒答 | 看 `logs/deepseek.log` 的 `finish=`；调大 `MAX_OUTPUT_TOKENS_CAP` 或关思考 |
| 反复 `输出被截断` | `max_tokens` 不够（思考模式常见） | 关掉该步思考，或把 `THINKING_TOKEN_HEADROOM` 调大 |
| 审核窗口不弹出 | GUI 线程异常（旧版本已修） | 升级到当前版本；错误区会显示异常类型 |
| 「source 在译文中不存在」 | 模型给的片段与译文有细微差异 | 条目仍会显示为「待人工确认」，可在窗口里改或跳过 |
| 译文里还有英文名 | 替换表没覆盖 / 未通过审核 | 用 `audit-nouns` 查命中，或在审核窗口补 |
| `a black and white 崽` 这类中英混排 | 中间态正常 | 最终翻译步骤会处理；若成品仍有，检查该步是否失败 |
| Word 里章节重复 | 删了 `progress.json` 后重跑 | 从 `progress.json` 里删对应条目才会重跑，重跑会再追加一章 |
| 章名不是四个字 | 模型没遵守约束 | 日志 WARNING；`TITLE_MODE=manual` 手动选 |
| 成本比预估高很多 | 思考模式 / 输出被截断重试 / 高峰时段 | 看 `cost_log.csv` 的 `retry_count`、`price_tier`、`completion_tokens` |
| 控制台中文乱码 | Windows 控制台代码页 | `$env:PYTHONUTF8='1'`（PowerShell） |
| 界面文字显示成方框 | 缺中文字体（多见于远程/虚拟机） | 安装「微软雅黑」等中文字体 |

---

## 14. 测试

```powershell
.\.venv311\Scripts\python.exe -m pytest tests -q      # 136 passed（约 5 秒，零成本）
```

测试全部使用临时目录，不写项目目录、不联网、不花钱。覆盖：

| 测试文件 | 覆盖内容 |
|---|---|
| `test_doc_parser.py` | 章头/副标题、前置 Notes、章末 Notes、无 Notes、无 Chapter Text 容错、Notes 里的假章头、多章连续 |
| `test_replacer.py` | 模式 A 全词/大小写/连锁/空删除/中英混排边界/长度优先/幂等；模式 B 精确匹配/缺失告警/标点提示/预览 |
| `test_chunker.py` | token 估算、段落装箱、超长段降级、样本节选、峰谷判定、下一个空闲时段、动态额度 |
| `test_client.py` | 正常 JSON、空 content、非法 JSON 降级、429 重试、401 不重试、500 重试上限、截断扩容、缓存命中、预算闸门、思考参数、缺密钥 |
| `test_ai_steps.py` | 分块顺序与上下文衔接、每步模型覆盖、跨块去重、短语展开、空条目过滤、章名校验、估算是上界 |
| `test_pipeline_e2e.py` | 11 步端到端、步骤 5 以 raw 为基准（等值断言）、Word 结构、幂等重跑、章号口径、空章失败 |
| `test_clean_mode.py` | 成功后只剩 Word/表/记录、失败保留现场、关闭清理时保留全部 |
| `test_noun_table_io.py` | TXT/JSON/GBK 导入、去重合并、导出往返、编辑器增删改查与去重 |
| `test_review_ui.py` | 三个窗口的 Space/Enter/←→/F6/Esc、空译名保护、缺失 source 标记、预览内容 |
| `test_gui_flow.py` | **主窗口 → 后台线程 → 审核窗口 → 回填**的完整链路、章节列表搜索、每步模型控件 |
| `test_acceptance.py` | 无浏览器自动化依赖、OpenAI 只在客户端实例化、密钥只在环境变量、提示词含业务约束、配置校验、产物命名 |

---

## 15. 打包与分发

### 15.1 源码包（推荐用于二次开发）

```powershell
git archive --format=zip -o TranslationFlow-src.zip HEAD
# 或直接压缩项目目录（排除 .venv / work / output / logs / .cache）
```

仓库已含 `pyproject.toml`，也可安装成包：

```powershell
python -m pip install .
translationflow          # 启动 GUI
translationflow-cli --help
```

### 15.2 独立 exe（给不装 Python 的人用）

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\packaging\build.ps1                 # 产物在 dist\TranslationFlow\
```

- 采用 **onedir** 而不是 onefile：启动快，且 `config.json` / `data` / `logs` 与 exe 同级，
  便于直接改配置；
- `packaging/TranslationFlow.spec` 里已经把 `config.example.json`、`samples/`、
  `data/noun_table.json` 一起打进去；
- 体积约 150~250MB（PySide6 为主）；首次运行会在同目录生成 `logs/` 与 `output/`。

---

## 16. 项目结构

```
TranslationFlow/
├─ main.py                     GUI 入口
├─ cli.py                      命令行入口（13 个子命令）
├─ pyproject.toml              包元数据 + 两个命令行入口
├─ requirements.txt
├─ config.json                 当前生效配置（含密钥以外的一切）
├─ config.example.json         配置模板
├─ .env.example                密钥模板（.env 已在 .gitignore）
├─ README.md
├─ tf/
│  ├─ config.py                配置加载/校验、每步模型解析、密钥写入
│  ├─ logger.py                分模块 RotatingFileHandler
│  ├─ doc_parser.py            .txt/.docx 读取、章头识别、正文摘取
│  ├─ replacer.py              模式 A / 模式 B / 预览
│  ├─ noun_table.py            替换表读写、TXT/JSON 导入导出
│  ├─ chunker.py               分块、token 估算、样本节选
│  ├─ sizing.py                体量画像、动态输出额度
│  ├─ cost.py                  记账、峰谷计价、预算闸门
│  ├─ audit.py                 替换表体检
│  ├─ prompts.py               四个提示词 + JSON schema
│  ├─ deepseek_client.py       唯一 API 出口（重试/降级/缓存/记账）
│  ├─ ai_steps.py              步骤 3/6/7/10 的业务封装
│  ├─ pipeline.py              11 步流水线、循环、暂停/停止、清理
│  ├─ word_writer.py           python-docx 写章
│  ├─ review/                  三个审核窗口 + 自动审核 + 演示数据
│  └─ gui/                     主控窗口、替换表编辑器、线程桥接
├─ tools/                      诊断与运维脚本（见 6 章）
├─ tests/                      136 项测试
├─ samples/                    示例源文档与文风样本
├─ data/noun_table.json        替换表
└─ output/                     译文输出目录（运行后生成）
```

---

## 17. 已知限制

1. **Windows 优先**：GUI 与"双击打开日志"依赖 Windows；其它平台可跑 CLI。
2. **单价需自行同步**：`PRICE_TABLE` 按 2026-11 官方价目表填写，官方调价后需手动更新
   （`cli.py pricing` 可核对）。
3. **`HOLIDAYS` 需自己填**：不填只会让节假日按工作日计价（成本略高估），不影响译文。
4. **思考模式默认关闭**（与需求书原始建议不同）：实测 5 倍价差、质量无肉眼差异；
   需要时可逐步勾选。
5. **润色采用短语级输出**（`POLISH_OUTPUT_MODE=phrase`）：严格按需求书的"模型给整句
   source/target"会产生大量 `source==target` 的空条目（一章 7000+ token）。想回到整句模式
   把该项改成 `sentence`。
6. **人名译名由模型推荐**：仍建议逐条过目，尤其是 `-kit/-paw/-star` 之外的例外命名。
7. **多块翻译**在真实文档里尚未触发过（本测试文档最长章 2372 词 < 6000 token 预算）；
   想验证可把 `MAX_INPUT_TOKENS_PER_CHUNK` 调成 1200 再跑一章。
8. **不处理插图、脚注、斜体等富文本**：源文档按纯文本处理，Word 输出为纯段落。
9. **不自动上传/发布**：产物只写到本地 `output/`。

---

## 18. 版本

| 版本 | 说明 |
|---|---|
| 2.0.0 | 完整 11 步流水线、峰谷计价、短语级润色、替换表编辑器、可滚动界面、干净运行模式、136 项测试 |
