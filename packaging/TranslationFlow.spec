# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置（onedir：启动快，配置/日志就近放）。

用法（在仓库根目录）：
    .\packaging\build.ps1
产物：
    dist\TranslationFlow\TranslationFlow.exe
"""

from pathlib import Path

ROOT = Path(SPECPATH).parent

datas = [
    (str(ROOT / "config.example.json"), "."),
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "LICENSE"), "."),
    (str(ROOT / "data" / "noun_table.json"), "data"),
    (str(ROOT / "profiles"), "profiles"),
    (str(ROOT / "samples"), "samples"),
]

hiddenimports = [
    "tf",
    "tf.config",
    "tf.logger",
    "tf.doc_parser",
    "tf.replacer",
    "tf.noun_table",
    "tf.chunker",
    "tf.sizing",
    "tf.cost",
    "tf.audit",
    "tf.prompts",
    "tf.deepseek_client",
    "tf.ai_steps",
    "tf.pipeline",
    "tf.word_writer",
    "tf.review",
    "tf.review.common",
    "tf.review.noun_review_dialog",
    "tf.review.polish_review_dialog",
    "tf.review.title_dialog",
    "tf.review.demo",
    "tf.gui",
    "tf.gui.main_window",
    "tf.gui.noun_table_dialog",
    "tf.gui.reviewer_bridge",
    "docx",
    "dotenv",
    "openai",
]

a = Analysis(
    [str(ROOT / "main.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "pytest", "PySide6.QtWebEngineCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TranslationFlow",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="TranslationFlow",
)
