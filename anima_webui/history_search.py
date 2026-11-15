"""Rebuildable content indexes over saved generation records.

This module never interprets an image or consults today's prompt catalog. Each
term keeps the attributes of the same saved character, including old aliases.
"""
from __future__ import annotations

import json
import sqlite3
import unicodedata
from typing import Any


INDEX_VERSION = 1
POOLS = ("character", "clothing", "pose", "background", "expression")
ATTRIBUTES = ("gender", "hair", "eye", "series")
TABLES = {"history_search_documents", "history_content_terms"}
INDEXES = {"idx_history_content_term", *(f"idx_history_content_{name}" for name in ATTRIBUTES)}


def normalize(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).casefold().strip()


def needs_upgrade(connection: sqlite3.Connection) -> bool:
    tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not TABLES.issubset(tables):
        return True
    indexes = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    if not INDEXES.issubset(indexes):
        return True
    return connection.execute(
        "SELECT 1 FROM history_search_documents WHERE index_version != ? LIMIT 1", (INDEX_VERSION,)
    ).fetchone() is not None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _selection(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def write_document(
    connection: sqlite3.Connection, image_id: int, positive_prompt: str,
    resolved_prompt: str, selection: Any,
) -> None:
    """Write within the caller's transaction, without committing it."""
    document = [positive_prompt or "", resolved_prompt or ""]
    rows: dict[tuple[str, str], tuple[Any, ...]] = {}
    for pool, entries in _selection(selection).items():
        if pool not in POOLS or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not _text(entry.get("id")):
                continue
            term_id = _text(entry["id"])
            names = [_text(entry.get(key)) for key in ("title_zh", "title", "subtitle_zh", "subtitle")]
            title = next((value for value in names if value), term_id)
            subtitle = next((value for value in (names[2], names[3], names[1]) if value and value != title), "")
            document.extend(names)
            labels = {field: _text(entry.get("copyright" if field == "series" else field)) for field in ATTRIBUTES}
            rows[(pool, term_id)] = (
                image_id, pool, term_id, title, subtitle,
                normalize("\n".join([*names, term_id])),
                *(normalize(labels[field]) for field in ATTRIBUTES),
                json.dumps(labels, ensure_ascii=False),
            )
    connection.execute("DELETE FROM history_content_terms WHERE image_id=?", (image_id,))
    connection.execute(
        "INSERT INTO history_search_documents(image_id,index_version,search_text) VALUES(?,?,?) "
        "ON CONFLICT(image_id) DO UPDATE SET index_version=excluded.index_version,search_text=excluded.search_text",
        (image_id, INDEX_VERSION, normalize("\n".join(document))),
    )
    connection.executemany(
        "INSERT INTO history_content_terms "
        "(image_id,pool,term_id,title,subtitle,search_text,gender,hair,eye,series,attribute_labels_json) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)", rows.values(),
    )


def initialize(connection: sqlite3.Connection) -> None:
    """Create/backfill atomically, including when a SQLite DDL operation fails."""
    owns_transaction = not connection.in_transaction
    if owns_transaction:
        connection.execute("BEGIN IMMEDIATE")
    connection.execute("SAVEPOINT history_content_index")
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rebuild = not TABLES.issubset(tables)
        connection.execute(
            "CREATE TABLE IF NOT EXISTS history_search_documents ("
            "image_id INTEGER PRIMARY KEY REFERENCES images(id) ON DELETE CASCADE,"
            "index_version INTEGER NOT NULL,search_text TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS history_content_terms ("
            "image_id INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,"
            "pool TEXT NOT NULL,term_id TEXT NOT NULL,title TEXT NOT NULL,subtitle TEXT NOT NULL,"
            "search_text TEXT NOT NULL,gender TEXT NOT NULL,hair TEXT NOT NULL,eye TEXT NOT NULL,series TEXT NOT NULL,"
            "attribute_labels_json TEXT NOT NULL,PRIMARY KEY(image_id,pool,term_id))"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_history_content_term ON history_content_terms(pool,term_id,image_id)"
        )
        for attribute in ATTRIBUTES:
            connection.execute(
                f"CREATE INDEX IF NOT EXISTS idx_history_content_{attribute} "
                f"ON history_content_terms(pool,{attribute},image_id)"
            )
        # One document also marks records without structured terms as indexed.
        records = connection.execute(
            "SELECT images.id,images.positive_prompt,images.resolved_prompt,images.resolved_selection_json "
            "FROM images LEFT JOIN history_search_documents d ON d.image_id=images.id "
            "WHERE ? OR d.image_id IS NULL OR d.index_version != ?", (rebuild, INDEX_VERSION),
        ).fetchall()
        for row in records:
            write_document(connection, row[0], row[1], row[2], row[3])
        connection.execute("RELEASE SAVEPOINT history_content_index")
        if owns_transaction:
            connection.commit()
    except Exception:
        if owns_transaction:
            connection.rollback()
        else:
            connection.execute("ROLLBACK TO SAVEPOINT history_content_index")
            connection.execute("RELEASE SAVEPOINT history_content_index")
        raise


def _values(values: Any, *, normalized: bool = False) -> str:
    if not isinstance(values, (list, tuple, set)) or any(not isinstance(item, str) for item in values):
        raise ValueError("内容筛选值必须为字符串列表")
    return json.dumps(sorted({normalize(value) if normalized else value for value in values}), ensure_ascii=False)


def query_conditions(q: str = "", content_filter: dict[str, Any] | None = None) -> tuple[list[str], tuple[Any, ...]]:
    conditions: list[str] = []
    parameters: list[Any] = []
    if normalize(q):
        conditions.append(
            "EXISTS (SELECT 1 FROM history_search_documents d WHERE d.image_id=images.id AND instr(d.search_text,?)>0)"
        )
        parameters.append(normalize(q))
    filters = content_filter or {}
    includes, excludes, attributes = (filters.get(name) or {} for name in ("include", "exclude", "attributes"))
    if any(not isinstance(value, dict) for value in (includes, excludes, attributes)):
        raise ValueError("内容筛选格式无效")
    if (set(includes) | set(excludes)) - set(POOLS) or set(attributes) - set(ATTRIBUTES):
        raise ValueError("未知的内容筛选分类")
    for pool in POOLS:
        term_conditions = ["t.pool=?"]
        term_parameters: list[Any] = [pool]
        selected = pool in includes
        if selected:
            term_conditions.append("t.term_id IN (SELECT value FROM json_each(?))")
            term_parameters.append(_values(includes[pool]))
        if pool == "character":
            for attribute in ATTRIBUTES:
                attribute_filter = attributes.get(attribute) or {}
                if not isinstance(attribute_filter, dict):
                    raise ValueError("人物属性筛选格式无效")
                if attribute_filter.get("include"):
                    selected = True
                    term_conditions.append(f"t.{attribute} IN (SELECT value FROM json_each(?))")
                    term_parameters.append(_values(attribute_filter["include"], normalized=True))
        if selected:
            # Materialize the matching image IDs once. A correlated EXISTS can
            # make SQLite probe every member of a large group for every image.
            conditions.append("images.id IN (SELECT t.image_id FROM history_content_terms t WHERE " + " AND ".join(term_conditions) + ")")
            parameters.extend(term_parameters)
        if excludes.get(pool):
            conditions.append(
                "images.id NOT IN (SELECT t.image_id FROM history_content_terms t WHERE t.pool=? "
                "AND t.term_id IN (SELECT value FROM json_each(?)))"
            )
            parameters.extend((pool, _values(excludes[pool])))
    for attribute in ATTRIBUTES:
        values = (attributes.get(attribute) or {}).get("exclude")
        if values:
            conditions.append(
                "images.id NOT IN (SELECT t.image_id FROM history_content_terms t WHERE t.pool='character' "
                f"AND t.{attribute} IN (SELECT value FROM json_each(?)))"
            )
            parameters.append(_values(values, normalized=True))
    return conditions, tuple(parameters)


def filter_options(
    connection: sqlite3.Connection, section: str, *, q: str = "", page: int = 1,
    limit: int = 40, term_ids: list[str] | None = None, attribute: str | None = None,
) -> dict[str, Any]:
    if section not in POOLS or attribute is not None and (section != "character" or attribute not in ATTRIBUTES):
        raise ValueError("未知的内容筛选分类")
    page, limit = max(1, int(page)), min(100, max(1, int(limit)))
    conditions, parameters = ["pool=?"], [section]
    if term_ids is not None:
        conditions.append("term_id IN (SELECT value FROM json_each(?))")
        parameters.append(_values(term_ids))
    key = attribute or "term_id"
    if attribute:
        conditions.append(f"{attribute} != '' AND {attribute} != 'unknown'")
    where = " AND ".join(conditions)
    # Keep all aliases searchable while selecting the latest snapshot's label.
    rows = connection.execute(
        f"WITH candidates AS (SELECT *,ROW_NUMBER() OVER(PARTITION BY {key} ORDER BY image_id DESC,term_id) AS latest "
        f"FROM history_content_terms WHERE {where}), "
        f"counts AS (SELECT {key} AS id,COUNT(DISTINCT image_id) AS uses,"
        + ("1" if attribute else "MAX(instr(search_text,?)>0)")
        + f" AS matches FROM candidates GROUP BY {key}) "
        f"SELECT c.*,counts.uses FROM candidates c JOIN counts ON counts.id=c.{key} "
        "WHERE c.latest=1 AND counts.matches=1",
        (*parameters, *((normalize(q),) if not attribute else ())),
    ).fetchall()
    options = []
    for row in rows:
        value = dict(row)
        if attribute:
            labels = json.loads(value["attribute_labels_json"])
            title = labels.get(attribute) or value[attribute]
            if attribute == "gender":
                title = {"1girl": "女性", "1boy": "男性"}.get(value[attribute], title)
            if normalize(q) not in normalize(title) and normalize(q) not in value[attribute]:
                continue
            options.append({"id": value[attribute], "title": title, "subtitle": "", "count": value["uses"]})
        else:
            options.append({"id": value["term_id"], "title": value["title"], "subtitle": value["subtitle"], "count": value["uses"]})
    # SQLite's lower()/NOCASE do not implement full Unicode casefold ordering.
    options.sort(key=lambda item: (-item["count"], normalize(item["title"]), item["id"]))
    total = len(options)
    offset = (page - 1) * limit
    return {"items": options[offset:offset + limit], "page": page, "limit": limit,
            "total": total, "has_more": offset + limit < total}
