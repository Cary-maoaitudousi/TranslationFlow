"""桌面入口。

    python main.py              启动图形界面
    python main.py --selftest   不联网自检（校验资源路径与界面能否构建），
                                结果写到 logs/selftest.txt，便于验证打包产物

打包后（PyInstaller）双击 TranslationFlow.exe 等价于 python main.py。
"""

from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _selftest() -> int:
    """无界面自检：确认配置路径、资源文件、界面构建都正常。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    lines: list[str] = []
    ok = True
    try:
        from tf.config import PROJECT_ROOT, load_config

        cfg = load_config(None)
        lines.append(f"frozen={getattr(sys, 'frozen', False)}")
        lines.append(f"PROJECT_ROOT={PROJECT_ROOT}")
        for key in ("OUTPUT_DOC_PATH", "GLOBAL_NOUN_TABLE_PATH", "STYLE_SAMPLE_PATH"):
            path = cfg.path(key)
            lines.append(f"{key}={path} exists={path.exists()}")

        from PySide6.QtWidgets import QApplication

        from tf.gui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        win = MainWindow()
        lines.append(f"window_title={win.windowTitle()}")
        lines.append(f"steps={win.l_steps.count()}")
        win.close()
        _ = app
    except Exception as exc:  # noqa: BLE001
        ok = False
        lines.append(f"ERROR {type(exc).__name__}: {exc}")
        lines.append(traceback.format_exc())

    root = Path(os.environ.get("TF_SELFTEST_DIR", ".")).resolve()
    try:
        from tf.config import PROJECT_ROOT as proj

        root = proj
    except Exception:  # noqa: BLE001
        pass
    out = root / "logs" / "selftest.txt"
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass
    return 0 if ok else 1


def main() -> int:
    if "--selftest" in sys.argv:
        return _selftest()
    from tf.gui.main_window import main as gui_main

    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
