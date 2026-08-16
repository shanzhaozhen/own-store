"""打印记录。

记录是给第二阶段收费用的，但更要紧的一条规矩是：
**记录写不进去也不能影响打印本身** —— 纸已经出来了，这时候抛异常最没用。
"""

from __future__ import annotations

from datetime import datetime

from shop_print.core import history


def test_记一条能读回来(tmp_path) -> None:
    db = tmp_path / "history.db"
    history.init(db)
    assert history.record(
        history.PrintJob(
            file_name="房屋租赁合同.pdf", pages=3, copies=2, duplex=True, printer="柯美225i"
        ),
        db,
    )

    rows = history.recent(db_path=db)
    assert len(rows) == 1
    row = rows[0]
    assert row["file_name"] == "房屋租赁合同.pdf"
    assert row["pages"] == 3
    assert row["copies"] == 2
    assert row["duplex"] == 1
    assert row["printer"] == "柯美225i"
    assert row["source"] == "desktop"  # 第二阶段小程序来的会填 miniprogram


def test_没建过表也能直接记(tmp_path) -> None:
    """不依赖 init 先跑过。启动顺序变了不该让记录整段丢失。"""
    db = tmp_path / "未初始化.db"
    assert history.record(history.PrintJob(file_name="a.pdf", pages=1), db)
    assert len(history.recent(db_path=db)) == 1


def test_最近的排在最前面(tmp_path) -> None:
    db = tmp_path / "history.db"
    for name in ("第一份.pdf", "第二份.pdf", "第三份.pdf"):
        history.record(history.PrintJob(file_name=name, pages=1), db)
    assert [row["file_name"] for row in history.recent(db_path=db)] == [
        "第三份.pdf",
        "第二份.pdf",
        "第一份.pdf",
    ]


def test_limit生效(tmp_path) -> None:
    db = tmp_path / "history.db"
    for index in range(5):
        history.record(history.PrintJob(file_name=f"{index}.pdf", pages=1), db)
    assert len(history.recent(limit=2, db_path=db)) == 2


def test_当天统计按张数算(tmp_path) -> None:
    """张数 = 页数 × 份数。收费按张，不是按份。"""
    db = tmp_path / "history.db"
    history.record(history.PrintJob(file_name="a.pdf", pages=3, copies=2, amount=1.2), db)
    history.record(history.PrintJob(file_name="b.pdf", pages=1, copies=1, amount=0.2), db)

    jobs, sheets, amount = history.day_summary(db_path=db)
    assert jobs == 2
    assert sheets == 7
    assert abs(amount - 1.4) < 1e-9


def test_失败的单子不计入统计(tmp_path) -> None:
    db = tmp_path / "history.db"
    history.record(history.PrintJob(file_name="卡纸了.pdf", pages=5, ok=False, amount=1.0), db)
    assert history.day_summary(db_path=db) == (0, 0, 0.0)
    assert len(history.recent(db_path=db)) == 1  # 但记录本身要留着，方便查为什么


def test_别的日期不算进今天(tmp_path) -> None:
    db = tmp_path / "history.db"
    history.record(history.PrintJob(file_name="a.pdf", pages=1), db)
    昨天 = datetime.now().replace(year=datetime.now().year - 1).strftime("%Y-%m-%d")
    assert history.day_summary(昨天, db_path=db) == (0, 0, 0.0)


def test_写不进去只返回False不抛(tmp_path) -> None:
    """用目录顶住数据库文件名，制造一个必然失败的写入。"""
    db = tmp_path / "占位.db"
    db.mkdir()
    assert history.record(history.PrintJob(file_name="a.pdf", pages=1), db) is False
    assert history.recent(db_path=db) == []
    assert history.day_summary(db_path=db) == (0, 0, 0.0)
