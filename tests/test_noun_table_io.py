"""替换表导入与人工编辑测试（TXT → JSON 统一存储）。"""

from __future__ import annotations

import json

import pytest

from tf.noun_table import (
    NounTable, export_table_text, load_table_file, parse_table_text,
)

TXT_SAMPLE = """ThunderClan->雷族
ShadowClan->影族

# 这是注释，应被忽略
Firestar->火星
ThunderClan->雷族
没有箭头的一行
Longtail -> 长尾
"""


def test_parse_txt_table_basic():
    entries, bad = parse_table_text(TXT_SAMPLE)
    sources = [e["source"] for e in entries]
    assert sources == ["ThunderClan", "ShadowClan", "Firestar", "ThunderClan", "Longtail"]
    assert entries[-1]["target"] == "长尾"        # 箭头两侧空格被清掉
    assert bad == ["没有箭头的一行"]


def test_import_txt_file_and_merge(tmp_path):
    txt = tmp_path / "table.txt"
    txt.write_text(TXT_SAMPLE, encoding="utf-8")
    entries, bad = load_table_file(txt)
    assert len(entries) == 5 and bad

    table = NounTable(tmp_path / "table.json", [])
    added, skipped = table.add(entries)
    assert len(added) == 4          # ThunderClan 出现两次
    assert len(skipped) == 1
    table.save()

    data = json.loads((tmp_path / "table.json").read_text(encoding="utf-8"))
    assert data["entries"][0] == {"source": "ThunderClan", "target": "雷族"}
    assert len(data["entries"]) == 4


def test_import_json_file_supported(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(
        json.dumps({"entries": [{"source": "Aaa", "target": "甲"},
                                {"source": "", "target": "坏"}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    entries, bad = load_table_file(src)
    assert entries == [{"source": "Aaa", "target": "甲"}]
    assert len(bad) == 1


def test_import_bare_json_array(tmp_path):
    src = tmp_path / "in.json"
    src.write_text(json.dumps([{"source": "Bbb", "target": "乙"}], ensure_ascii=False),
                   encoding="utf-8")
    entries, bad = load_table_file(src)
    assert entries == [{"source": "Bbb", "target": "乙"}]
    assert bad == []


def test_export_roundtrip(tmp_path):
    entries = [{"source": "Firestar", "target": "火星"}, {"source": "Longtail", "target": "长尾"}]
    text = export_table_text(entries)
    assert text == "Firestar->火星\nLongtail->长尾"
    back, bad = parse_table_text(text)
    assert back == entries and bad == []


def test_import_gbk_encoded_txt(tmp_path):
    txt = tmp_path / "gbk.txt"
    txt.write_bytes("ThunderClan->雷族\n".encode("gb18030"))
    entries, _ = load_table_file(txt)
    assert entries == [{"source": "ThunderClan", "target": "雷族"}]


def test_editor_dialog_saves_table(qapp, tmp_path):
    from tf.gui.noun_table_dialog import NounTableDialog

    path = tmp_path / "noun.json"
    path.write_text(json.dumps({"entries": [{"source": "Aaa", "target": "甲"}]},
                               ensure_ascii=False), encoding="utf-8")
    dlg = NounTableDialog(path)
    assert dlg.table.rowCount() == 1

    dlg._on_add()                       # 新增空行
    assert dlg.table.rowCount() == 2
    dlg.table.item(1, 0).setText("Bbb")
    dlg.table.item(1, 1).setText("乙")

    dlg.search.setText("Bbb")           # 搜索只显示一条
    assert dlg.table.rowCount() == 1
    dlg.search.clear()
    assert dlg.table.rowCount() == 2

    dlg._on_save()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert {e["source"] for e in data["entries"]} == {"Aaa", "Bbb"}
    assert dlg.saved_count() == 2


def test_editor_merges_case_duplicates(qapp, tmp_path):
    from tf.gui.noun_table_dialog import NounTableDialog

    path = tmp_path / "noun.json"
    path.write_text(
        json.dumps({"entries": [
            {"source": "ThunderClan", "target": "雷族"},
            {"source": "thunderClan", "target": "雷族"},
            {"source": "Firestar", "target": "火星"},
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )
    dlg = NounTableDialog(path)
    assert dlg.table.rowCount() == 3
    dlg._on_dedup()
    assert dlg.table.rowCount() == 2
    sources = [r[0] for r in dlg.rows]
    assert "ThunderClan" in sources and "thunderClan" not in sources


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
