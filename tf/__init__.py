"""TranslationFlow：猫武士同人章节 英译中 自动化流水线。

模块划分（对应需求书 §2.1）：
    config         配置加载与校验
    logger         分级日志（按模块分文件）
    doc_parser     章节切分与正文摘取
    replacer       模式 A / 模式 B 替换
    noun_table     全局专有名词表
    chunker        长文本分块与 token 估算
    cost           成本记录、预算闸门、价格表
    prompts        四个 AI 步骤的提示词与 JSON schema
    deepseek_client  统一 DeepSeek/OpenAI 兼容客户端
    ai_steps       步骤 3/6/7/10 的业务封装
    word_writer    Word 输出
    pipeline       单章流水线与循环控制
    review         人工审核（GUI 弹窗 / 无头自动审核）
"""

__version__ = "2.0.0"
