"""四个 AI 步骤的提示词与 JSON schema。

提示词本身**不写死任何作品设定**：命名规则、称谓、文化适配要求都来自
``tf/fandom.py`` 加载的 IP 档案（``profiles/*.json``）。换作品只换档案文件。

约定：
* 每个 system prompt 都包含 "json" 字样、JSON 样例与"只输出 JSON"；
* 解析代码与这里的 schema 一一对应（见 ``tf/prompts.py`` 的 ``JSON_SCHEMAS``）；
* 模板用 ``__XXX__`` 占位符 + ``str.replace`` 渲染，避免 JSON 花括号被当成格式字段。
"""

from __future__ import annotations

from .fandom import FandomProfile


# ---------------------------------------------------------------------------
# 步骤 3：识别未翻译的新专有名词
# ---------------------------------------------------------------------------
def nouns_system(profile: FandomProfile) -> str:
    return f"""你是一名资深的小说翻译家，负责「{profile.name}」相关作品的专有名词译名审定。

任务：从下面这段文本中找出**尚未译成中文的英文专有名词**，并给出推荐译名。
提取范围：{profile.noun_scope}。普通英文单词不要提取。

命名规则：
{profile.numbered_rules()}

译法参考（示范风格，不要照抄）：
{profile.naming_examples_text()}

补充要求：
- 文本中已经是中文的名字不要重复提取；同一个名字只输出一次。
- 推荐译名必须是纯中文，不含英文、不含括号注释。
- 有官方或通行译名时优先采用，保证全书前后一致。

输出格式：只输出一个 JSON 对象，不要输出任何解释文字、不要使用 Markdown 代码块。
JSON 结构：
{{"entries": [{{"source": "英文原名", "target": "推荐中文"}}]}}

字段说明：source 为文本中出现的英文原名（与原文完全一致）；target 为推荐中文译名。
若没有需要提取的专有名词，输出 {{"entries": []}}。"""


NOUNS_USER_TEMPLATE = """以下是从小说章节中摘取的文本（已知专有名词已被替换为中文）。请提取其中尚未翻译的英文专有名词，并给出推荐中文译名。

只输出 JSON，结构为 {"entries": [{"source": "...", "target": "..."}]}。

待处理文本：
<<<
__TEXT__
>>>"""


# ---------------------------------------------------------------------------
# 步骤 6：全文翻译
# ---------------------------------------------------------------------------
def translate_system(profile: FandomProfile, style_sample: str, is_excerpt: bool = False) -> str:
    if not style_sample.strip():
        style_sample = "（暂无文风样本，请使用自然、克制、具有文学性的中文叙事风格。）"
    note = "（样本为节选）" if is_excerpt else ""
    brief = profile.translation_brief
    domain = f"\n作品设定：{profile.domain_note}" if profile.domain_note else ""
    return f"""{profile.translate_role}。{domain}

作为同人文翻译流水线的一环，我已经完成了一部分翻译工作，并积累了文风参考样本。请学习这些已有译文的叙事节奏、用词偏好和句式特征，在后续翻译中保持一致。

注意：仅学习和处理我提供的翻译内容，不引入外部无关信息。确保翻译的准确性和一致性，尊重原作的风格与语境。

以下为文风参考样本{note}：
<<<
{style_sample}
>>>

硬性要求：
1. {brief}。
2. 译文必须是纯中文，不要残留英文单词或英文解释；对话引号使用中文引号“”。
3. 保留原文的分段结构（段落之间用空行分隔）。
4. 只输出一个 JSON 对象，不要输出任何解释文字、不要使用 Markdown 代码块。

输出 JSON 结构：
{{"translation": "纯中文译文全文", "notes": ["可选：无法处理的残留专有名词等备注"]}}

字段说明：translation 为纯中文译文全文；notes 为可选字符串数组，可为空数组。"""


TRANSLATE_USER_TEMPLATE = """请把下面的章节文本翻译为中文。

只输出 JSON，结构为 {"translation": "...", "notes": []}。

__CONTEXT_BLOCK__待翻译文本：
<<<
__TEXT__
>>>"""

TRANSLATE_CONTEXT_BLOCK = """参考上下文（上一段已完成的译文，仅供衔接语气与用词参考，**不要翻译、不要重复输出**）：
<<<
__PREV__
>>>

"""


# ---------------------------------------------------------------------------
# 步骤 7：检测文化不适配表达（短语级，默认）
# ---------------------------------------------------------------------------
def polish_system(profile: FandomProfile) -> str:
    """短语级润色：只让模型报"要改的短语"，上下文本地展开。"""
    return f"""你是一名资深的小说翻译家，负责「{profile.name}」相关作品的译文润色。

任务：找出译文中与作品设定不符的**人类中心表达**，给出替换方案。判定标准：
{profile.culture_rules_text()}

输出方式：只报**需要改的那一小段文字**（短语级），不要整句照抄，更不要列出无需修改的内容。
- phrase：输入译文中的一段连续原文，必须逐字符一模一样（含标点），长度建议 2~12 字，能唯一确定要改的位置即可；
- replacement：把 phrase 改成什么，只需给出改后的那一小段，不需要上下文；
- context：不超过 30 字的说明。

示例（示范粒度与风格，不要照抄）：
{profile.culture_examples_text()}

硬性要求：
1. phrase 必须能在输入译文中原样找到，逐字符一致，不得改标点。
2. 只报确实需要改的地方，通常一章（3000 字左右）只有 3~12 处；单次最多输出 12 条。没有就输出空数组。
3. 不要输出与修改无关的句子；不要为了凑数而列出。
4. 只输出一个 JSON 对象，不要输出任何解释文字、不要使用 Markdown 代码块。

输出 JSON 结构：
{{"entries": [{{"phrase": "原文片段", "replacement": "改法", "context": "说明"}}]}}"""


POLISH_SENTENCE_SYSTEM_TEMPLATE = """你是一名资深的小说翻译家，负责「__PROFILE__」相关作品的译文润色。

任务：找出译文中与作品设定不符的**人类中心表达**（如人体肢体、“人”字、人类习语、不合适的量词），给出替换方案。判定标准：
__RULES__

输出要求：左原文严格保持原文内容不变（含标点），右边改写；target 要与前后文标点自然衔接。
- source 必须与输入译文逐字符一致，并带足够上下文以唯一定位（建议 15~40 字）；
- 只输出确实需要改写的条目，source 与 target 相同的条目一律不要输出；
- 单次最多输出 15 条，没有就输出空数组。

示例（示范风格，不要照抄）：
__EXAMPLES__

硬性要求：
1. source 逐字符一致，不得改动标点。
2. 只输出一个 JSON 对象，不要输出解释文字、不要使用 Markdown 代码块。

输出 JSON 结构：
{{"entries": [{{"source": "原表达（含上下文）", "target": "改写", "context": "不超过30字的说明"}}]}}"""


def polish_sentence_system(profile: FandomProfile) -> str:
    return (
        POLISH_SENTENCE_SYSTEM_TEMPLATE.replace("__PROFILE__", profile.name)
        .replace("__RULES__", profile.culture_rules_text())
        .replace("__EXAMPLES__", profile.culture_examples_text())
    )


POLISH_USER_TEMPLATE = """以下是中文译文。请找出其中与作品设定不符、会让读者出戏的表达，给出替换方案。

只输出 JSON，结构为 {"entries": [{"source": "...", "target": "...", "context": "..."}]}。

译文：
<<<
__TEXT__
>>>"""

POLISH_PHRASE_USER_TEMPLATE = """以下是中文译文。请找出与作品设定不符的人类中心表达，按"短语 → 改法"给出方案。

只输出 JSON，结构为 {"entries": [{"phrase": "...", "replacement": "...", "context": "..."}]}。

译文：
<<<
__TEXT__
>>>"""


# ---------------------------------------------------------------------------
# 步骤 10：推荐章节名称
# ---------------------------------------------------------------------------
def titles_system(profile: FandomProfile) -> str:
    return f"""每当给出一个「{profile.name}」的章节，你需要分析该章节的主要内容、核心要旨，提炼主要冲突，并据此构思章节名。

要求：
1. {profile.title_rule}。
2. 每次输出 **5 个**，风格各不相同（如意象象征、谶语悬念、场景叙事、反讽对照、主题提炼）。
3. 每个标题必须**恰好四个汉字**，不含标点、数字或英文。
4. 每个标题配一句不超过 40 字的理由。
5. 只输出示例中的内容，不要给出思考过程。

输出格式：只输出一个 JSON 对象，不要输出任何解释文字、不要使用 Markdown 代码块。
JSON 结构：
{{"titles": [{{"style": "风格", "title": "四字标题", "reason": "一句理由"}}]}}"""


TITLES_USER_TEMPLATE = """以下是一章「__PROFILE__」的中文译文（可能是节选）。请分析其主要内容、核心要旨与主要冲突，给出 5 个不同风格的四字章节名及理由。

只输出 JSON，结构为 {"titles": [{"style": "...", "title": "....", "reason": "..."}]}。

章节译文：
<<<
__TEXT__
>>>"""


# ---------------------------------------------------------------------------
# JSON 重试加固（不是新步骤，仅用于解析失败后的第二次尝试）
# ---------------------------------------------------------------------------
JSON_REMINDER = """警告：上一次输出无法被解析为 JSON。请重新回答，并且**只**输出合法 JSON 对象本身：
- 不要 Markdown 代码块（不要 ```），不要前后缀说明文字；
- 所有字符串必须正确转义（引号用 \\" ，换行用 \\n）；
- 严格遵循此前给出的 JSON schema：__SCHEMA__"""

JSON_SCHEMAS = {
    "nouns": '{"entries": [{"source": "英文名", "target": "中文名"}]}',
    "translate": '{"translation": "纯中文译文", "notes": []}',
    "polish": '{"entries": [{"source": "原表达", "target": "替换", "context": "说明"}]}',
    "polish_phrase": '{"entries": [{"phrase": "原文片段", "replacement": "改法", "context": "说明"}]}',
    "titles": '{"titles": [{"style": "风格", "title": "四字名", "reason": "理由"}]}',
}


# ---------------------------------------------------------------------------
# 用户消息渲染
# ---------------------------------------------------------------------------
def render_nouns_user(text: str) -> str:
    return NOUNS_USER_TEMPLATE.replace("__TEXT__", text)


def render_translate_user(text: str, previous_translation: str = "") -> str:
    context = ""
    if previous_translation.strip():
        context = TRANSLATE_CONTEXT_BLOCK.replace(
            "__PREV__", previous_translation.strip()[-1200:]
        )
    return TRANSLATE_USER_TEMPLATE.replace("__CONTEXT_BLOCK__", context).replace("__TEXT__", text)


def render_polish_user(text: str) -> str:
    return POLISH_USER_TEMPLATE.replace("__TEXT__", text)


def render_polish_phrase_user(text: str) -> str:
    return POLISH_PHRASE_USER_TEMPLATE.replace("__TEXT__", text)


def render_titles_user(text: str, profile_name: str = "作品", char_limit: int = 6000) -> str:
    # 章名只需要内容概要，超长章节截取首尾，节省输入 token
    limit = max(1000, int(char_limit))
    if len(text) > limit:
        head = text[: int(limit * 0.65)]
        tail = text[-int(limit * 0.35):]
        text = f"{head}\n\n……（中间略）……\n\n{tail}"
    return TITLES_USER_TEMPLATE.replace("__PROFILE__", profile_name).replace("__TEXT__", text)


def render_json_reminder(schema_key: str) -> str:
    return JSON_REMINDER.replace("__SCHEMA__", JSON_SCHEMAS.get(schema_key, "{}"))
