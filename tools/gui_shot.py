"""渲染界面截图（不启动流水线、不花钱）。

用法：
    python tools/gui_shot.py output/gui.png            # 主控窗口
    python tools/gui_shot.py output/table.png --table  # 替换表编辑器
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    out = Path(args[0] if args else "output/gui.png")
    want_table = "--table" in argv
    app = QApplication.instance() or QApplication(sys.argv[:1])

    if want_table:
        from tf.config import load_config
        from tf.gui.noun_table_dialog import NounTableDialog

        cfg = load_config("config.json")
        win = NounTableDialog(cfg.path("GLOBAL_NOUN_TABLE_PATH"))
    else:
        from tf.gui.main_window import MainWindow

        win = MainWindow()

    win.show()
    for _ in range(6):
        app.processEvents()
    out.parent.mkdir(parents=True, exist_ok=True)
    win.grab().save(str(out))
    print(f"已保存：{out}")
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
