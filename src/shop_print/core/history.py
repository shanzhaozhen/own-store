"""打印记录（SQLite）。

现在只是为了统计和查账，但**从第一天就记** —— 第二阶段小程序收费
要直接用这张表，等到需要时再补就没有历史数据了。
见 docs/08-第二阶段-微信小程序.md。
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .. import paths

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS print_jobs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT    NOT NULL,
    file_name    TEXT    NOT NULL,
    source       TEXT    NOT NULL DEFAULT 'desktop',  -- desktop | miniprogram
    pages        INTEGER NOT NULL,
    copies       INTEGER NOT NULL,
    duplex       INTEGER NOT NULL DEFAULT 0,
    paper        TEXT    NOT NULL DEFAULT 'A4',
    printer      TEXT    NOT NULL DEFAULT '',
    amount       REAL    NOT NULL DEFAULT 0,
    ok           INTEGER NOT NULL DEFAULT 1,
    note         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_print_jobs_created_at ON print_jobs(created_at);
"""


@dataclass
class PrintJob:
    file_name: str
    pages: int
    copies: int = 1
    duplex: bool = False
    paper: str = "A4"
    printer: str = ""
    amount: float = 0.0
    ok: bool = True
    source: str = "desktop"
    note: str = ""


def _connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or paths.history_db()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init(db_path: Path | None = None) -> None:
    with closing(_connect(db_path)) as conn, conn:
        conn.executescript(_SCHEMA)


def record(job: PrintJob, db_path: Path | None = None) -> bool:
    """记一条。**记录失败绝不能影响打印本身** —— 纸已经出来了。"""
    try:
        with closing(_connect(db_path)) as conn, conn:
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT INTO print_jobs "
                "(created_at, file_name, source, pages, copies, duplex, paper, printer, amount, ok, note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    job.file_name,
                    job.source,
                    job.pages,
                    job.copies,
                    int(job.duplex),
                    job.paper,
                    job.printer,
                    job.amount,
                    int(job.ok),
                    job.note,
                ),
            )
    except sqlite3.Error:
        logger.exception("打印记录写入失败：%s", job.file_name)
        return False
    return True


def recent(limit: int = 50, db_path: Path | None = None) -> list[sqlite3.Row]:
    try:
        with closing(_connect(db_path)) as conn:
            conn.executescript(_SCHEMA)
            return list(
                conn.execute(
                    "SELECT * FROM print_jobs ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            )
    except sqlite3.Error:
        logger.exception("读打印记录失败")
        return []


def day_summary(day: str | None = None, db_path: Path | None = None) -> tuple[int, int, float]:
    """某天的 (单数, 总张数, 总金额)。day 形如 2026-08-16，缺省为今天。"""
    target = day or datetime.now().strftime("%Y-%m-%d")
    try:
        with closing(_connect(db_path)) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT COUNT(*) AS jobs, "
                "COALESCE(SUM(pages * copies), 0) AS sheets, "
                "COALESCE(SUM(amount), 0) AS amount "
                "FROM print_jobs WHERE ok = 1 AND created_at LIKE ?",
                (f"{target}%",),
            ).fetchone()
    except sqlite3.Error:
        logger.exception("统计打印记录失败")
        return (0, 0, 0.0)
    return (int(row["jobs"]), int(row["sheets"]), float(row["amount"]))
