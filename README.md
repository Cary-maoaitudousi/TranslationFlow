# TranslationFlow

**把一部（或一整个系列的）英文长篇/同人作品，按章自动翻成中文并导成 Word。**
适合那些**专有名词特别多、靠通用 AI 翻译很难保证前后一致**的作品——
通过「你的替换表 + 你的 IP 档案 + 两道人工审核」，把专有名词的译法钉死，再让大模型翻译正文。

> 项目名就是目录名：`TranslationFlow`。不绑定任何具体作品：
> 命名规则、角色称谓、文化适配要求全部放在 `profiles/*.json` 里，换作品只换档案文件。

---

## 它适合什么 / 不适合什么

| 适合 | 不适合 |
|---|---|
| 系列同人（几十上百章、同一批角色反复出现） | 单篇短文、一次性翻译 |
| 人名/地名/组织名/头衔数量大（几百条）且需要全书统一 | 专有名词很少的普通文章 |
| 已有官方译名或粉丝通行译名，需要沿用 | 没有既定译名、也不在意一致性 |
| 作品世界观特殊（动物、机械、异种），通用翻译会"出戏" | 完全现实题材的纯文学 |
| 想控制成本、想看清楚每章花了多少钱 | 需要图文混排、脚注、注释系统的排版工程 |

一句话判断：**"人名译错一个，读者就会出戏"的作品，用它。**

---

## 目录

| 章节 | 内容 |
|---|---|
| [1. 工作原理](#1-工作原理) | 11 步流程与每步产物 |
| [2. IP 档案](#2-ip-档案核心概念) | 命名规则/称谓/文化适配怎么配置 |
| [3. 环境与安装](#3-环境与安装) | Python、依赖、密钥 |
| [4. 第一次运行](#4-第一次运行) | 六步跑通第一章 |
| [5. 主控界面](#5-主控界面) | 每个区域做什么 |
| [6. 审核窗口](#6-审核窗口) | 快捷键与审核语义 |
| [7. 命令行参考](#7-命令行参考) | 13 个子命令 |
| [8. 配置项全表](#8-配置项全表) | 每个键的默认值与含义 |
| [9. 替换表维护](#9-替换表维护) | TXT/JSON 导入、编辑器、体检 |
| [10. 文风样本](#10-文风样本) | 如何提供、如何被截取 |
| [11. 提示词与 JSON 契约](#11-提示词与-json-契约) | 四个 AI 步骤的输入输出 |
| [12. 成本控制](#12-成本控制) | 峰谷计价、实测数据、省钱的每一层 |
| [13. 运行产物](#13-运行产物) | 正常跑完到底留下什么 |
| [14. 日志与排错](#14-日志与排错) | 日志文件、错误分级、FAQ |
| [15. 测试](#15-测试) | 137 项测试覆盖什么 |
| [16. 打包与分发](#16-打包与分发) | 源码包 / 独立 exe |
| [17. 项目结构](#17-项目结构) | 文件树 |
| [18. 已知限制](#18-已知限制) | 边界与注意事项 |

---

## 1. 工作原理

```
                    ┌────────────────────── 每章循环 ──────────────────────┐
英文文档 ──①摘章──▶ raw.txt
                    │
                    ├─②模式A换名（你的替换表）──▶ replaced.txt
                    │
                    ├─③AI 找未翻译的专有名词（JSON）──▶ new_nouns.json
                    │
                    ├─④【人工审核】通过/改译名/跳过 ──▶ 追加进你的替换表
                    │
                    ├─⑤以 raw.txt 为基准再换一次名 ──▶ final_mixed.txt（中英混合）
                    │
                    ├─⑥AI 翻译（按段落分块，附文风样本 + IP 档案）──▶ translated.txt
                    │
                    ├─⑦AI 找不符合设定的表达（短语级）──▶ polish.json
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
| 3 | AI 识别未翻译专有名词 | 快模型 | 是 | `chapter_{n}_new_nouns.json` |
| 4 | **人工审核**专有名词 | 你 | 否 | `chapter_{n}_approved_nouns.json` + 追加全局表 |
| 5 | 二次专有名词替换（**基准 = raw**） | 本地 | 否 | `chapter_{n}_final_mixed.txt` |
| 6 | AI 翻译（分块 + 文风样本 + IP 档案） | 强模型 | 是 | `chapter_{n}_translated.txt` |
| 7 | AI 检测不符合设定的表达 | 强模型 | 是 | `chapter_{n}_polish.json` |
| 8 | **人工审核**润色表 | 你 | 否 | `chapter_{n}_polish_approved.json` |
| 9 | 润色替换（模式 B） | 本地 | 否 | `chapter_{n}_final_chinese.txt` |
| 10 | AI 推荐四字章名 | 快模型 | 是 | `chapter_{n}_titles.json` |
| 11 | 写入 Word | 本地 | 否 | `output/译文.docx`（追加） |

**为什么步骤 5 要以 `raw.txt` 为基准**：如果拿步骤 2 的输出再替换一次，连锁替换会二次污染
（A→B 之后 B 又被 B→C 命中）。所以每次都从原文重跑一遍完整替换表。

**"章"怎么数**：`START_CHAPTER` 指的是"文档里第几个章头"（1 基），不是章号。
Word 里写的是章头里的数字（`Chapter 100` → `第 100 章`），可用 `CHAPTER_LABEL_MODE=loop`
改成"本次循环的第几章"。

**章头识别**：默认正则 `^\s*Chapter\s+(\d+)`，并额外要求章号后面紧跟冒号/换行/分隔符，
所以章末 Notes 里的 `Chapter 100 babeee!!` 这类行不会被误判成新章。其它格式用
`CHAPTER_HEADING_PATTERN` 覆盖（如 `^\s*第\s*(\d+)\s*章`）。正文会自动去掉行首缩进。

---

## 2. IP 档案（核心概念）

**问题**：不同作品的差别不在"翻译"，而在"这部作品里人怎么叫、词怎么译"。
把这些写死在代码里，项目就只能服务一部作品。

**做法**：一个 JSON 档案描述这部作品的设定，提示词由档案渲染出来。

### 2.1 档案字段

| 字段 | 作用 | 示例（某动物奇幻作品） |
|---|---|---|
| `name` | 作品名，出现在提示词里 | `猫武士 / Warriors` |
| `subject_singular` / `subject_plural` | 角色在作品里怎么称呼 | `猫` / `猫` |
| `domain_note` | 一句话世界观 | `动物奇幻；角色是野猫，族群社会有自己的命名体系` |
| `naming_rules` | **命名规则数组**（最重要） | `幼崽名以 -kit 结尾，译为「XX崽」` |
| `naming_examples` | 译法示例 `英文->中文` | `Thistleclaw->蓟掌` |
| `noun_scope` | 要提取的专名范围 | `人名、地名、族群名、职位名` |
| `culture_rules` | **文化适配规则数组** | `人体肢体、“人”字要换成设定内的说法` |
| `culture_examples` | 改写示例 `原句->改后` | `两人陷入了沉默->两者陷入了沉默` |
| `title_rule` | 章名要求 | `四字章节名，5 种不同风格` |
| `translate_role` | 翻译角色设定 | `你是一位经验丰富的同人小说翻译家…` |
| `translation_brief` | 翻译硬要求 | `专有名词用给定中文，不要音译` |

### 2.2 内置档案

| 文件 | 说明 |
|---|---|
| `profiles/generic.json` | **默认**，通用兜底：不预设物种与设定，只保留"专名统一、量词/称谓一致"的通用规则 |
| `profiles/warriors.json` | 示例：猫武士（`-kit→崽`、`-paw→爪`、`-star→星`、量词"只"、人体表达替换） |
| `profiles/_template.json` | 新建档案的骨架，每行都有填写说明 |

### 2.3 新建一个档案（给新作品用）

1. 复制 `profiles/_template.json` → `profiles/你的作品.json`；
2. 填 `name`、`subject_*`、`naming_rules`（**至少 3 条**，这是译名一致性的关键）；
3. 填 `culture_rules` 与示例——这一步决定译文"像不像这部作品"；
4. 主控界面「IP 档案」选这个文件（或改 `config.json` 的 `FANDOM_PROFILE_PATH`）；
5. 点「重载」，状态行会显示档案名与规则条数；
6. 用 `python cli.py prompts --name nouns` 打印提示词，人工过一遍再开跑。

> 档案缺失或写坏不会崩：会自动回退到通用档案，并在日志与界面上给出橙色警告。
> 换作品时建议同时换一份**独立的替换表**（`GLOBAL_NOUN_TABLE_PATH`），避免译名串味。

### 2.4 档案怎么影响效果

- 步骤 3 的"人名识别"直接受 `naming_rules` 影响：规则写得越具体，推荐译名越贴合作品；
- 步骤 6 的"翻译"会带上 `translate_role` / `domain_note` / `translation_brief`；
- 步骤 7 的"文化适配"只按 `culture_rules` 判定什么是"出戏表达"——所以**不要**在通用档案下期待它自动识别"猫应该叫爪不叫手"；
- 步骤 10 的章名按 `title_rule` 生成。

---

## 3. 环境与安装

| 项 | 要求 |
|---|---|
| 操作系统 | Windows 10/11（界面用 PySide6；"打开日志"用 `os.startfile`，其它系统会退化为提示） |
| Python | 3.10 及以上 |
| 网络 | 能访问 `https://api.deepseek.com` |
| 磁盘 | 源码+依赖约 400MB（PySide6 为主）；缓存与日志通常 < 50MB |

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`requirements.txt`：`openai`（DeepSeek 兼容接口）、`python-docx`（读写 docx）、
`python-dotenv`（读 `.env`）、`PySide6-Essentials`（界面）、`pytest`（测试）。

### 密钥的三种放法（都不会进 git）

1. **主控界面**：填 API Key →「保存密钥」→ 写入 `.env`（已在 `.gitignore`）；
2. 手动：`Copy-Item .env.example .env`，填 `DEEPSEEK_API_KEY=`；
3. 临时：`$env:DEEPSEEK_API_KEY='sk-...'`。

Base URL 默认官方，走中转网关改 `DEEPSEEK_BASE_URL`。
**密钥只从环境变量/`.env` 读取，不写 `config.json`，不进日志。**

---

## 4. 第一次运行

```powershell
# ① 确认能解析源文档（不花钱）
python cli.py chapters -c config.json

# ② 导入你的替换表（TXT 或 JSON 都行）
python cli.py import-nouns -c config.json --txt 你的替换表.txt

# ③ 选/建 IP 档案（决定命名规则与文化适配）
python cli.py prompts -c config.json --name nouns   # 先看提示词是否符合预期

# ④ 估价（不花钱）
python cli.py estimate -c config.json --chapter 1

# ⑤ 零成本跑通全流程
python cli.py run -c config.json --mock --auto-approve --start 1 --count 1 -y

# ⑥ 打开界面正式跑第一章
python main.py
```

跑完检查：

| 位置 | 内容 |
|---|---|
| `output/译文.docx` | 第 1 章标题 + 正文分段 + 章末分页 |
| `data/noun_table.json` | 审核通过的新专名已合并进去 |
| `logs/cost_log.csv` | 本次调用的 token 与费用 |
| `logs/progress.json` | 第 1 章已完成（下次再跑会跳过） |

---

## 5. 主控界面

```
┌─ 输入输出 ────────────────────────────────────────────┐
│ 源文档（.txt/.docx）  [浏览…]                          │
│ 输出 Word             [浏览…]                          │
│ 全局专有名词表        [浏览…]                          │
│ 替换表   [导入替换表(TXT/JSON)…] [编辑替换表…]          │
│ 文风样本              [浏览…]                          │
│ IP 档案   [profiles/xxx.json] [浏览…][打开档案][重载]   │
│           当前档案：xxx｜命名规则 N 条｜…（绿色/橙色）   │
├─ 循环与预算 ──────────────────────────────────────────┤
│ 循环  起始章 [1] 章数 [1] 章号口径 [doc▾] 章名 [auto▾]  │
│ 成本  总预算 [10] 单章预算 [0.3] 分块 token [6000]     │
│ 开关  □MOCK □审核全自动 ☑跳过已完成 ☑成本优先 □等空闲   │
│ 操作  [保存配置][校验配置][预估成本][列出章节]          │
│ 法定节假日 [2026-01-01, …] [怎么填？]                  │
├─ 每一步的模型与思考模式 ──────────────────────────────┤
│ 步骤3 人名识别   [Fast▾] □开启   …（4 行）             │
├─ DeepSeek API ────────────────────────────────────────┤
│ API Key [••••] [保存密钥][测试连接]                    │
├─ 步骤清单 │ 运行日志 / 成本 / 错误（双击打开日志）────┤
└─ [开始][暂停][继续][停止]  状态：…   [进度条] ─────────┘
```

| 区域 | 说明 |
|---|---|
| **输入输出** | 四个路径 + **替换表**（导入/编辑）+ **IP 档案**（浏览/打开/重载，并显示当前档案名与规则条数） |
| **循环与预算** | 起始章/章数；`章号口径` doc=用原文数字，loop=用循环序；预算填 `0` 表示不限；分块 token 越小越省但调用次数多 |
| **开关** | `MOCK` 不联网；`审核全自动` 跳过人工确认；`跳过已完成` 复用进度；`成本优先` 峰谷计价；`等空闲` 高峰时等到空闲再开跑 |
| **法定节假日** | 只影响计价（填了的日期按空闲价），格式 `YYYY-MM-DD`，"怎么填？"里有说明 |
| **每一步的模型与思考** | 四步各自选快/强模型、是否开思考；「预估成本」按当前组合报价 |
| **DeepSeek API** | 填 Key → 保存/测试（测试会发一次最小请求，约 ¥0.0003） |
| **底部** | 控制按钮 + 11 步进度 + 进度条，**永远可见不随配置滚动** |
| **错误区** | 按章按步骤列出，**双击打开对应模块的日志** |

**布局行为**：配置区在滚动区内，窗口压小时出现滚动条；配置↔日志、日志↔错误之间都可拖动。

---

## 6. 审核窗口

| 按键 | 行为 |
|---|---|
| `Space` | 通过当前条并跳下一条（再按取消通过） |
| `Enter` | 进入编辑；再按 `Enter` 保存并跳下一条 |
| `←` / `→` | 上一条 / 下一条 |
| `F6` | 确认提交（**独立于 Enter**，防惯性误操作） |
| `Esc` | 取消（本章终止） |

**专有名词审核（步骤 4）**：`原文 | AI 推荐译名 | 状态`，下方实时显示"全局表 + 本次通过"
合并后的完整表；译名为空的条目按 `Space` 会被拒绝通过。

**润色审核（步骤 8）**：`原文片段 | 替换为 | 说明 | 状态`，下方预览"替换后受影响的段落"；
`source` 在译文中找不到的条目标为「待人工确认」，不阻断你继续审。

**章名选择（步骤 10，`TITLE_MODE=manual`）**：5 个候选，双击或 `Enter` 选定。

不花钱先看效果：

```powershell
python cli.py review-demo -c config.json                 # 依次弹出三个窗口
python cli.py review-demo -c config.json --capture out   # 渲染成 PNG
```

演示数据来自你真实的替换表、文风样本与 IP 档案，并埋了「空译名」「译文中不存在的 source」。

---

## 7. 命令行参考

`python cli.py <命令> [参数]`，`-c/--config` 可写在子命令前或后。

| 命令 | 作用 | 关键参数 |
|---|---|---|
| `chapters` | 列出源文档各章 | — |
| `estimate` | 单章成本预估（不调 API） | `--chapter N` |
| `run` | 执行流水线 | `-y` 免确认；`--mock`；`--auto-approve`；`--start/--count`；`--no-skip`；`--model-pro/--model-flash` |
| `report` | 累计成本（按章/按步） | — |
| `pricing` | 价目、当前档位、下一个空闲时段 | — |
| `prompts` | 打印提示词（**换档案后先看这个**） | `--name nouns/translate/polish/titles/all` |
| `import-nouns` | 导入 `英文->中文` 表 | `--txt 表.txt`、`--replace` |
| `export-nouns` | 导出为 TXT | `--txt 备份.txt` |
| `audit-nouns` | 替换表命中分布与误伤提示 | `--chapters 5` |
| `review-demo` | 打开/渲染审核窗口 | `--capture DIR` |
| `holidays` | 查看/设置法定节假日 | `--set "2026-01-01,…"` |
| `cache` | 查看/清空请求缓存 | `--clear` |
| `init` | 生成 `config.json` 与目录骨架 | `--source 文档.txt` |

```powershell
# 零成本回归
python cli.py run -c config.json --mock --auto-approve --start 1 --count 3 -y
# 真跑 5 章，人工审三次窗口
python cli.py run -c config.json --start 6 --count 5
# 全量（131 章，约 ¥6~9）
python cli.py run -c config.json --start 1 --count 131
```

> 所有 CLI 参数都能用同名环境变量覆盖配置，例如 `$env:LOOP_COUNT='5'`。

工具脚本（`tools/`）：`show_cost.py`（成本明细）、`show_polish.py`（某章润色改动）、
`compare_runs.py`（两次运行对比）、`inspect_docx.py`（产物结构检查）、
`total_spend.py`（汇总账单）、`footprint_check.py`（验证运行足迹）、
`gui_shot.py`（界面截图）、`probe_api.py`（连通性，¥0.003）、
`probe_step7.py` / `probe_polish_raw.py`（单步诊断）。

---

## 8. 配置项全表

优先级：**环境变量 > `config.json` > 代码默认值**。

### 8.1 路径、循环与档案

| 键 | 默认 | 说明 |
|---|---|---|
| `SOURCE_DOC_PATH` | `""` | 源文档（`.txt`/`.docx`/`.md`） |
| `OUTPUT_DOC_PATH` | `output/译文.docx` | 输出 Word，不存在则新建，存在则追加 |
| `GLOBAL_NOUN_TABLE_PATH` | `data/noun_table.json` | 全局替换表（跨章累积） |
| `STYLE_SAMPLE_PATH` | `data/style_sample.txt` | 文风样本 |
| `FANDOM_PROFILE_PATH` | `profiles/generic.json` | **IP 档案** |
| `START_CHAPTER` | `1` | 从第几个章头开始（1 基） |
| `LOOP_COUNT` | `1` | 本次处理章数 |
| `CHAPTER_HEADING_PATTERN` | `^\s*Chapter\s+(\d+)` | 章头正则，需含捕获组 |
| `CHAPTER_LABEL_MODE` | `doc` | `doc`=原文章号；`loop`=循环序 |

### 8.2 模型与思考

| 键 | 默认 | 说明 |
|---|---|---|
| `MODEL_FLASH` / `MODEL_PRO` | `deepseek-flash` / `deepseek-v4-pro` | 两档模型名 |
| `MODEL_STEP3/6/7/10` | `""` | 各步模型，空=按默认分级 |
| `THINKING_STEPS` | `[]` | 开思考的步骤号；实测开/关差 **5 倍价格** |
| `REASONING_EFFORT` | `high` | 思考强度（high/medium/low） |

### 8.3 重试与超时

| 键 | 默认 | 说明 |
|---|---|---|
| `MAX_RETRIES` | `5` | 429 等并发错误重试次数 |
| `RETRY_BACKOFF_BASE` / `RETRY_BACKOFF_MAX` | `1.0` / `60.0` | 指数退避基数/上限（秒） |
| `REQUEST_TIMEOUT_SEC` | `600` | 单请求超时 |

### 8.4 分块与输出额度

| 键 | 默认 | 说明 |
|---|---|---|
| `MAX_INPUT_TOKENS_PER_CHUNK` | `6000` | 每块输入预算 |
| `MAX_STYLE_SAMPLE_TOKENS` | `2000` | 文风样本预算（超出则摘代表性段落） |
| `CHARS_PER_TOKEN_EN` | `4.0` | token 估算系数 |
| `TITLE_INPUT_CHAR_LIMIT` | `6000` | 起章名只看译文首尾字符数 |
| `OUTPUT_TOKENS_PER_INPUT` | `1.7` | 输出额度系数 |
| `MIN_CHUNK_OUTPUT_TOKENS` / `MAX_OUTPUT_TOKENS_CAP` | `1200` / `8000` | 单块输出上下限 |
| `THINKING_TOKEN_HEADROOM` | `2500` | 思考模式的推理额度预留（否则会被截断） |
| `MAX_TOKENS_HARD_CAP` | `16000` | 截断重试的硬上限 |
| `TRUNCATION_RETRIES` | `2` | 截断后的扩容重试次数 |

### 8.5 润色

| 键 | 默认 | 说明 |
|---|---|---|
| `MAX_POLISH_ENTRIES` | `15` | 单章润色候选上限 |
| `POLISH_OUTPUT_MODE` | `phrase` | `phrase`=只报短语（省 token）；`sentence`=报整句 |
| `POLISH_CONTEXT_WINDOW` | `14` | 短语模式本地展开上下文的半宽（字） |

### 8.6 成本与峰谷

| 键 | 默认 | 说明 |
|---|---|---|
| `COST_PRIORITY` | `false` | 成本优先（按峰谷计价） |
| `WAIT_FOR_OFFPEAK` | `false` | 高峰时等到空闲时段再开跑 |
| `PRICING_TIER` | `auto` | `auto`/`peak`/`offpeak`（强制档位便于对账） |
| `PEAK_WINDOWS` | `["09:00-12:00","14:00-18:00"]` | 高峰时段（北京时间） |
| `PEAK_WEEKDAYS` | `[0,1,2,3,4]` | 高峰星期（0=周一），周末全天空闲 |
| `HOLIDAYS` | `[]` | 法定节假日（全天按空闲价） |
| `BUDGET_TOTAL_CNY` / `BUDGET_PER_CHAPTER_CNY` | `0` | 预算闸门，0=不限 |
| `CACHE_ENABLED` / `CACHE_DIR` | `true` / `.cache/deepseek` | 请求缓存 |

### 8.7 幂等与产物

| 键 | 默认 | 说明 |
|---|---|---|
| `SKIP_COMPLETED` | `true` | 已完成章节直接跳过 |
| `CLEAN_INTERMEDIATES` | `true` | 成功后删中间产物；失败保留现场 |
| `WORK_DIR` | `work` | 中间产物根目录（正常跑完为空） |
| `LOG_DIR` / `LOG_LEVEL` | `logs` / `INFO` | 日志目录与级别 |
| `COST_LOG_PATH` | `logs/cost_log.csv` | 账单 |
| `PROGRESS_PATH` | `logs/progress.json` | 已完成章节记录 |

### 8.8 审核与输出

| 键 | 默认 | 说明 |
|---|---|---|
| `AUTO_APPROVE` | `false` | 全自动审核 |
| `TITLE_MODE` | `auto` | `auto`=取第一条；`manual`=弹窗选 |
| `WORD_TITLE_TEMPLATE` | `第 {label} 章  {title}` | 标题模板 |
| `WORD_FONT_NAME` / `WORD_FONT_SIZE` / `WORD_LINE_SPACING` | `""`/`0`/`0` | 空=Word 默认样式 |
| `MOCK_MODE` | `false` | 不联网（联调） |

---

## 9. 替换表维护

### 9.1 两种格式，统一存 JSON

**TXT**（一行一条，`#` 注释，分隔符支持 `->`/`→`/`=>`/制表符，UTF-8/GBK 自动探测）：

```
Hogwarts->霍格沃茨
Ministry of Magic->魔法部
```

**JSON**（统一存储）：

```json
{"version": 1, "entries": [{"source": "Hogwarts", "target": "霍格沃茨"}]}
```

### 9.2 三个入口

| 入口 | 说明 |
|---|---|
| 界面「导入替换表（TXT/JSON）」 | 选文件 → 合并或覆盖 → 写回 JSON |
| 界面「编辑替换表」 | 表格增删改、搜索、导出 TXT、**合并大小写重复**、保存校验 |
| `cli.py import-nouns --txt 表.txt [--replace]` | 批量导入 |

### 9.3 替换规则

- **区分大小写**、**全词匹配**（ASCII 字母边界，中英混排也能命中）；
- **长词优先**（`Thistleclaw` 不会被 `Thistle` 吃掉）；
- `target` 为空 = 删除匹配项；
- 全小写的通用词（`kit`/`paw`/`moon`）会命中正文里的独立单词——
  `cli.py audit-nouns` 能看到上下文，不想保留就删；
- 同一英文名允许大小写两个变体共存（都会生效），编辑器可一键合并。

### 9.4 体检

```powershell
python cli.py audit-nouns -c config.json --chapters 5
```

输出命中最多的条目、从未命中的条目数、疑似误伤（通用词被当独立单词替换）及上下文。不调 API。

---

## 10. 文风样本

`STYLE_SAMPLE_PATH` 指向你已有的中文译文。它会进 system prompt 让模型学习
"叙事节奏、用词偏好、句式特征"。

- 超过 `MAX_STYLE_SAMPLE_TOKENS` 时自动摘取**开头 / 对话 / 战斗 / 心理**四类段落，
  并标注"样本为节选"；
- 缺失不会中断（WARNING），但风格会漂移；
- 它会在**每次分块调用**里重复出现（服务端上下文缓存能显著降低这部分成本）。

---

## 11. 提示词与 JSON 契约

四个步骤都强制 `response_format={"type":"json_object"}`，提示词里同时含 "json" 与样例。
提示词**由 IP 档案渲染**，代码里不写死任何作品（`tf/prompts.py` + `tf/fandom.py`）。

| 步骤 | 输入 | 输出 schema |
|---|---|---|
| 3 人名识别 | 已初步换名的文本 | `{"entries":[{"source","target"}]}` |
| 6 翻译 | 中英混合正文 + 文风样本 + 档案 | `{"translation":"…","notes":["…"]}` |
| 7 润色检测 | 纯中文译文 | `{"entries":[{"phrase","replacement","context"}]}` |
| 10 章名 | 译文（首尾节选） | `{"titles":[{"style","title","reason"}]}` |

**解析失败的处理顺序**（全自动）：空 content 视为失败 → `temperature=0` 重问并重申 schema →
逐行找 `->` 宽松解析 → 认不出的行写 `chapter_{n}_manual_review.log`，继续跑。

**输出被截断**（`finish_reason=length`）：扩容 `max_tokens` 重试；仍截断就自动关思考兜底
（推理 token 也计入 `max_tokens`）。

---

## 12. 成本控制

### 12.1 峰谷计价（按官方价目表校准）

| 元 / 百万 tokens | Flash 高峰 | Flash 空闲 | Pro 高峰 | Pro 空闲 |
|---|---|---|---|---|
| 输入（缓存命中） | 0.04 | 0.02 | 0.30 | 0.15 |
| 输入（缓存未命中） | 2.0 | 1.0 | 9.0 | 4.5 |
| 输出 | 8.0 | 4.0 | 27.0 | 13.5 |

高峰 = 周一至周五 9:00-12:00、14:00-18:00（北京时间，不含法定节假日）；其余含周末与节假日
全天按空闲价（恰为一半）。`python cli.py pricing` 显示当前档位与下一个空闲时段。

### 12.2 实测（某 131 章测试文档，单章 1300~2000 词）

| 配置 | 单章成本 | 耗时 | 131 章推算 |
|---|---|---|---|
| 思考开 + 句子级润色 | ¥0.54 | 386s | ≈ ¥70 |
| 思考关 + 句子级润色 | ¥0.28 | 147s | ≈ ¥37 |
| **思考关 + 短语级润色（默认）** | **¥0.046 ~ 0.066** | **32~50s** | **≈ ¥6 ~ 9** |

成本大头是**输出 token**（思考模式的推理 token 也算输出）。关掉后质量无肉眼差异。

### 12.3 省钱的每一层

| 手段 | 位置 | 效果 |
|---|---|---|
| 每步可选模型/思考 | 界面 / `MODEL_STEP*` | 只有翻译与润色用强模型 |
| 峰谷计价 + 等待 | `PRICING_TIER` / `WAIT_FOR_OFFPEAK` | 空闲价直接减半 |
| 预算闸门 | `BudgetGuard` | 调用前预检，超了立刻停 |
| 请求缓存 | `.cache/deepseek` | 同章重跑 0 花费 |
| 幂等跳过 | `logs/progress.json` | 已完成章节连解析都省 |
| 动态输出额度 | `tf/sizing.py` | `max_tokens` 随块大小变 |
| 短语级润色 | `POLISH_OUTPUT_MODE=phrase` | 输出从 7000+ 降到 ~500 token |
| 空条目过滤 | `ai_steps` | 丢掉 `source==target` 的凑数条目 |
| 章名只看首尾 | `TITLE_INPUT_CHAR_LIMIT` | 省一次整章输入 |
| 文风样本节选 | `MAX_STYLE_SAMPLE_TOKENS` | 控制 system prompt 体积 |
| 全程记账 | `logs/cost_log.csv` | 每次调用（含重试）一行 |

**先估价再开跑**：`cli.py estimate` 或界面「预估成本」；真跑前还会再问一次。

---

## 13. 运行产物

### 13.1 正常跑完一章

| 路径 | 性质 |
|---|---|
| `output/译文.docx` | **唯一实质产物**（多章累积） |
| `data/noun_table.json` | 审核通过后新增的专有名词 |
| `logs/cost_log.csv` | 账单（token/缓存命中/重试/费用/档位） |
| `logs/progress.json` | 已完成章节（用于跳过，避免重复写 Word） |
| `logs/*.log` | 六个模块的滚动日志（10MB × 5 备份） |
| `.cache/deepseek/` | 请求缓存（`cli.py cache --clear` 可清） |

中间产物不残留：`CLEAN_INTERMEDIATES=true` 时成功后删除 `work/chapter_n/`；
**失败时保留现场**（raw / translated / summary 都在）。

验证：

```powershell
python tools/footprint_check.py before
python cli.py run -c config.json --mock --auto-approve --start 1 --count 1 -y
python tools/footprint_check.py after
```

### 13.2 失败时保留什么

`work/chapter_n/` 下 11 步产物 + `chapter_n_summary.json`（每步耗时/token/重试/降级标记/费用、
审核统计、分块计划、峰谷档位）。

### 13.3 清空重来

```powershell
Remove-Item -Recurse -Force work, output, .cache
Remove-Item logs\progress.json      # 忘记"已完成"
```

---

## 14. 日志与排错

| 文件 | 内容 |
|---|---|
| `logs/main.log` | 编排、成本、异常堆栈 |
| `logs/doc_parser.log` | 章头识别、正文范围、Notes |
| `logs/replacer.log` | 模式 A/B 命中与未命中 |
| `logs/deepseek.log` | 每次调用：模型/思考/输入(命中)/输出/重试/finish/json_ok/档位/费用/耗时 |
| `logs/review.log` | 审核与替换表读写 |
| `logs/word.log` | Word 写入 |

### 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| `DEEPSEEK_API_KEY 未设置` | 没密钥 | 界面「保存密钥」或写 `.env` |
| `401` | 密钥错/过期 | 重新填，「测试连接」验证 |
| 步骤 6 `译文为空` | 被截断或拒答 | 看日志 `finish=`；调大 `MAX_OUTPUT_TOKENS_CAP` 或关思考 |
| 反复 `输出被截断` | `max_tokens` 不够 | 关该步思考，或调大 `THINKING_TOKEN_HEADROOM` |
| 译名风格不对 / 不符合作品 | 档案不对 | 改 `FANDOM_PROFILE_PATH`，用 `cli.py prompts` 复核提示词 |
| 界面状态行橙色警告 | 档案缺失/损坏 | 选一个有效 `profiles/*.json`，点「重载」 |
| 审核窗口不弹出 | GUI 线程异常 | 错误区会显示异常类型与堆栈 |
| 「source 在译文中不存在」 | 模型给的片段有细微差异 | 条目显示为「待人工确认」，可改可跳过 |
| 译文里还有英文名 | 替换表没覆盖 | `audit-nouns` 查命中，或在审核窗口补 |
| Word 里章节重复 | 删 `progress.json` 后重跑 | 这是预期：重跑会再追加一章 |
| 章名不是四个字 | 模型没遵守 | 日志 WARNING；`TITLE_MODE=manual` 手动选 |
| 控制台中文乱码 | Windows 代码页 | `$env:PYTHONUTF8='1'` |
| 界面文字成方框 | 缺中文字体 | 装「微软雅黑」等字体 |

---

## 15. 测试

```powershell
.\.venv311\Scripts\python.exe -m pytest tests -q      # 137 passed（约 5 秒，零成本）
```

全部使用临时目录、不联网、不花钱。覆盖：文档解析边界、两种替换模式、分块与动态额度、
客户端重试/降级/截断/缓存/预算、四个 AI 步骤的拼装与校验、11 步端到端、干净运行模式、
替换表导入导出与编辑器、三个审核窗口的快捷键、**GUI 线程到弹窗的完整链路**、
以及"提示词必须由 IP 档案驱动、不得写死作品"的静态检查。

---

## 16. 打包与分发

### 16.1 源码包

```powershell
git archive --format=zip -o TranslationFlow-src.zip HEAD
```

或 `pip install .` 后使用两个入口：`translationflow`（GUI）、`translationflow-cli`。

### 16.2 独立 exe（给不装 Python 的人）

```powershell
.\packaging\build.ps1                 # 产物：dist\TranslationFlow\TranslationFlow.exe
```

- **onedir**（启动快），配置与资源摆在 exe 同级：`config.json`、`data/`、`profiles/`、
  `samples/`、`logs/`、`output/`；
- 打包后 `PROJECT_ROOT` 自动指向 exe 所在目录，改配置即改 exe 旁边的文件；
- 构建末尾会跑一次 `--selftest`（不联网），结果写到 `logs/selftest.txt`。

---

## 17. 项目结构

```
TranslationFlow/
├─ main.py                    GUI 入口（--selftest 自检）
├─ cli.py                     命令行入口（13 个子命令）
├─ pyproject.toml             包元数据 + 两个 console script
├─ config.json / config.example.json
├─ .env.example
├─ profiles/                  IP 档案：generic / warriors / _template
├─ tf/
│  ├─ fandom.py               IP 档案加载与字段
│  ├─ config.py               配置、每步模型解析、密钥写入
│  ├─ logger.py               分模块滚动日志
│  ├─ doc_parser.py           .txt/.docx 读取、章头识别、正文摘取
│  ├─ replacer.py             模式 A / 模式 B / 预览
│  ├─ noun_table.py           替换表读写、TXT/JSON 导入导出
│  ├─ chunker.py              分块、token 估算、样本节选
│  ├─ sizing.py               体量画像、动态输出额度
│  ├─ cost.py                 记账、峰谷计价、预算闸门
│  ├─ audit.py                替换表体检
│  ├─ prompts.py              提示词渲染（档案驱动）+ JSON schema
│  ├─ deepseek_client.py      唯一 API 出口（重试/降级/缓存/记账）
│  ├─ ai_steps.py             步骤 3/6/7/10 的业务封装
│  ├─ pipeline.py             11 步流水线、循环、暂停/停止、清理
│  ├─ word_writer.py          python-docx 写章
│  ├─ review/                 三个审核窗口 + 自动审核 + 演示数据
│  └─ gui/                    主控窗口、替换表编辑器、线程桥接
├─ tools/                     诊断与运维脚本
├─ tests/                     137 项测试
├─ samples/                   自制示例源文档与文风样本
├─ data/noun_table.json       替换表
└─ output/                    译文输出（运行后生成）
```

---

## 18. 已知限制

1. **Windows 优先**：界面与"打开日志/档案"依赖 Windows；其它平台可用 CLI。
2. **单价需自行同步**：`PRICE_TABLE` 按 2026-11 官方价目表填写，官方调价后需更新。
3. **`HOLIDAYS` 需自己填**：不填只会让节假日按工作日计价（略高估成本），不影响译文。
4. **思考模式默认关闭**：实测 5 倍价差、质量无肉眼差异；需要时逐步勾选。
5. **润色默认短语级**：整句模式会产生大量 `source==target` 空条目；想要整句改
   `POLISH_OUTPUT_MODE=sentence`。
6. **译名仍建议人工过目**：尤其是不符合命名规则的例外情况。
7. **多块翻译**在 2000 词级章节不触发（1 块即够）；想验证把
   `MAX_INPUT_TOKENS_PER_CHUNK` 调成 1200 再跑一章。
8. **不处理富文本**：源文档按纯文本处理，输出为纯段落（无插图/脚注/斜体）。
9. **不自动发布**：产物只写本地 `output/`。
10. **IP 档案是效果上限**：档案写得粗糙，译名与语气就会飘；建议先 `cli.py prompts` 预览。

---

## 19. 版本

| 版本 | 说明 |
|---|---|
| 2.0.0 | 11 步流水线、IP 档案驱动、峰谷计价、短语级润色、替换表编辑器、可滚动界面、干净运行模式、137 项测试、一键打包 |
