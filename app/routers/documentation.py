"""Documentation - pipeline documentation with entity linking and auto-suggest."""

import json
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request

from app.database import get_db
from app.models import (
    DocumentationOut, DocumentationCreate, DocumentationUpdate,
    DocEntityLinkInfo,
)
from app.routers.eventlog import log_event, get_actor

router = APIRouter(prefix="/api/documentation", tags=["documentation"])

# Entity type -> (table, name_col)
_ENTITY_TABLES = {
    "report": ("reports", "name"),
    "source": ("sources", "name"),
    "script": ("scripts", "display_name"),
    "upstream": ("upstream_systems", "name"),
}


def _resolve_links(db, doc_id: int) -> list[DocEntityLinkInfo]:
    """Resolve doc_entity_links into DocEntityLinkInfo with names."""
    rows = db.execute(
        "SELECT entity_type, entity_id FROM doc_entity_links WHERE doc_id = ?",
        (doc_id,),
    ).fetchall()
    links = []
    for r in rows:
        etype = r["entity_type"]
        eid = r["entity_id"]
        name = None
        tbl_info = _ENTITY_TABLES.get(etype)
        if tbl_info:
            tbl, name_col = tbl_info
            row = db.execute(f"SELECT {name_col} FROM {tbl} WHERE id = ?", (eid,)).fetchone()
            if row:
                name = row[name_col]
        links.append(DocEntityLinkInfo(entity_type=etype, entity_id=eid, entity_name=name))
    return links


def _save_links(db, doc_id: int, links):
    """Replace all entity links for a doc."""
    db.execute("DELETE FROM doc_entity_links WHERE doc_id = ?", (doc_id,))
    for link in links:
        db.execute(
            "INSERT OR IGNORE INTO doc_entity_links (doc_id, entity_type, entity_id) VALUES (?, ?, ?)",
            (doc_id, link.entity_type, link.entity_id),
        )


def _build_out(db, row) -> DocumentationOut:
    report_name = None
    if row["report_id"]:
        rpt = db.execute("SELECT name FROM reports WHERE id = ?", (row["report_id"],)).fetchone()
        if rpt:
            report_name = rpt["name"]
    return DocumentationOut(
        id=row["id"],
        report_id=row["report_id"],
        report_name=report_name,
        title=row["title"],
        business_purpose=row["business_purpose"],
        business_audience=row["business_audience"],
        business_cadence=row["business_cadence"],
        technical_lineage_mermaid=row["technical_lineage_mermaid"],
        technical_sources=row["technical_sources"],
        technical_transformations=row["technical_transformations"],
        technical_known_issues=row["technical_known_issues"],
        information_tab=row["information_tab"],
        status=row["status"],
        created_by=row["created_by"],
        linked_entities=_resolve_links(db, row["id"]),
        archived=bool(row["archived"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("", response_model=list[DocumentationOut])
def list_docs(include_archived: bool = Query(False)):
    with get_db() as db:
        filt = "" if include_archived else "WHERE archived = 0"
        rows = db.execute(f"SELECT * FROM documentation {filt} ORDER BY title").fetchall()
        return [_build_out(db, r) for r in rows]


@router.get("/options")
def get_doc_options():
    """Return dropdown options for the create/edit form."""
    with get_db() as db:
        people = db.execute("SELECT id, name, role FROM people ORDER BY name").fetchall()
        reports = db.execute(
            "SELECT id, name FROM reports WHERE archived = 0 ORDER BY name"
        ).fetchall()
        sources = db.execute(
            "SELECT id, name FROM sources WHERE archived = 0 ORDER BY name"
        ).fetchall()
        scripts = db.execute(
            "SELECT id, display_name FROM scripts WHERE archived = 0 ORDER BY display_name"
        ).fetchall()
        upstreams = db.execute(
            "SELECT id, name FROM upstream_systems WHERE archived = 0 ORDER BY name"
        ).fetchall()
    return {
        "people": [dict(r) for r in people],
        "reports": [dict(r) for r in reports],
        "sources": [{"id": r["id"], "name": r["name"]} for r in sources],
        "scripts": [{"id": r["id"], "name": r["display_name"]} for r in scripts],
        "upstreams": [dict(r) for r in upstreams],
        "statuses": ["draft", "published"],
        "cadences": ["Daily", "Weekly", "Bi-weekly", "Monthly", "Quarterly", "Yearly", "Ad-hoc"],
    }


@router.get("/suggest/{report_id}")
def suggest_doc(report_id: int):
    """Auto-generate documentation suggestion from existing governance data."""
    with get_db() as db:
        report = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

        # Tables and sources
        tables = db.execute("""
            SELECT rt.table_name, s.id AS source_id, s.name AS source_name, s.type AS source_type,
                   us.name AS upstream_name
            FROM report_tables rt
            LEFT JOIN sources s ON s.id = rt.source_id
            LEFT JOIN upstream_systems us ON us.id = s.upstream_id
            WHERE rt.report_id = ?
            ORDER BY rt.table_name
        """, (report_id,)).fetchall()

        # Scripts that write to any of these sources
        source_ids = [t["source_id"] for t in tables if t["source_id"]]
        scripts = []
        if source_ids:
            placeholders = ",".join("?" * len(source_ids))
            scripts = db.execute(f"""
                SELECT DISTINCT sc.id, sc.display_name, st.table_name, st.direction
                FROM script_tables st
                JOIN scripts sc ON sc.id = st.script_id
                WHERE st.source_id IN ({placeholders})
                ORDER BY sc.display_name
            """, source_ids).fetchall()

        # Scheduled tasks linked to these scripts
        script_ids = list(set(s["id"] for s in scripts))
        sched_tasks = []
        if script_ids:
            placeholders = ",".join("?" * len(script_ids))
            sched_tasks = db.execute(f"""
                SELECT task_name, schedule_type, script_id
                FROM scheduled_tasks
                WHERE script_id IN ({placeholders}) AND archived = 0
            """, script_ids).fetchall()

        # Key measures (top 20 by name)
        measures = db.execute("""
            SELECT table_name, measure_name, measure_dax
            FROM report_measures
            WHERE report_id = ?
            ORDER BY table_name, measure_name
            LIMIT 20
        """, (report_id,)).fetchall()

        # Build Mermaid diagram
        mermaid_lines = ["graph LR"]
        seen_nodes = set()

        for st in sched_tasks:
            node_id = f"sched_{st['script_id']}"
            if node_id not in seen_nodes:
                mermaid_lines.append(f'    {node_id}["{_mesc(st["task_name"])}"]:::task')
                seen_nodes.add(node_id)

        for s in scripts:
            node_id = f"script_{s['id']}"
            if node_id not in seen_nodes:
                mermaid_lines.append(f'    {node_id}["{_mesc(s["display_name"])}"]:::script')
                seen_nodes.add(node_id)

        for st in sched_tasks:
            mermaid_lines.append(f"    sched_{st['script_id']} --> script_{st['script_id']}")

        upstreams_seen = set()
        for t in tables:
            if t["upstream_name"] and t["upstream_name"] not in upstreams_seen:
                node_id = f"us_{t['upstream_name'].replace(' ', '_')}"
                mermaid_lines.append(f'    {node_id}["{_mesc(t["upstream_name"])}"]:::upstream')
                upstreams_seen.add(t["upstream_name"])

            if t["source_name"]:
                src_node = f"src_{t['source_id']}"
                if src_node not in seen_nodes:
                    mermaid_lines.append(f'    {src_node}["{_mesc(t["source_name"])}"]:::source')
                    seen_nodes.add(src_node)
                if t["upstream_name"]:
                    us_node = f"us_{t['upstream_name'].replace(' ', '_')}"
                    mermaid_lines.append(f"    {us_node} --> {src_node}")

        for s in scripts:
            for t in tables:
                if t["source_id"]:
                    src_node = f"src_{t['source_id']}"
                    mermaid_lines.append(f"    script_{s['id']} --> {src_node}")
                    break

        report_node = f"report_{report_id}"
        mermaid_lines.append(f'    {report_node}["{_mesc(report["name"])}"]:::report')
        for t in tables:
            if t["source_id"]:
                mermaid_lines.append(f"    src_{t['source_id']} --> {report_node}")

        mermaid_lines.append("    classDef task fill:#fbbf24,color:#000")
        mermaid_lines.append("    classDef script fill:#c4b5fd,color:#000")
        mermaid_lines.append("    classDef upstream fill:#fb923c,color:#000")
        mermaid_lines.append("    classDef source fill:#34d399,color:#000")
        mermaid_lines.append("    classDef report fill:#60a5fa,color:#000")

        # Build sources summary
        sources_summary = []
        for t in tables:
            if t["source_name"]:
                sources_summary.append({
                    "name": t["source_name"],
                    "type": t["source_type"],
                    "table": t["table_name"],
                    "upstream": t["upstream_name"],
                })

        # Build transformations text from measures
        transformations = ""
        if measures:
            lines = []
            for m in measures:
                dax = m["measure_dax"] or ""
                if len(dax) > 200:
                    dax = dax[:200] + "..."
                lines.append(f"**{m['measure_name']}** ({m['table_name']})\n{dax}")
            transformations = "\n\n".join(lines)

    return {
        "title": report["name"],
        "business_cadence": report["frequency"],
        "technical_lineage_mermaid": "\n".join(mermaid_lines),
        "technical_sources": json.dumps(sources_summary, indent=2),
        "technical_transformations": transformations,
        "linked_entities": [{"entity_type": "report", "entity_id": report_id}],
    }


def _mesc(text: str) -> str:
    """Escape text for Mermaid node labels."""
    if not text:
        return ""
    return text.replace('"', "'").replace("\n", " ")[:60]


@router.get("/{doc_id}", response_model=DocumentationOut)
def get_doc(doc_id: int):
    with get_db() as db:
        row = db.execute("SELECT * FROM documentation WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Documentation not found")
        return _build_out(db, row)


@router.post("", response_model=DocumentationOut)
def create_doc(req: DocumentationCreate, request: Request):
    now = datetime.now(timezone.utc).isoformat()
    actor = get_actor(request)
    try:
        with get_db() as db:
            cursor = db.execute(
                """INSERT INTO documentation
                   (report_id, title, business_purpose, business_audience, business_cadence,
                    technical_lineage_mermaid, technical_sources, technical_transformations,
                    technical_known_issues, information_tab, status, created_by,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (req.report_id, req.title, req.business_purpose, req.business_audience,
                 req.business_cadence, req.technical_lineage_mermaid, req.technical_sources,
                 req.technical_transformations, req.technical_known_issues, req.information_tab,
                 req.status, actor, now, now),
            )
            doc_id = cursor.lastrowid
            _save_links(db, doc_id, req.linked_entities)
            log_event(db, "documentation", doc_id, req.title, "created",
                      f"status={req.status}", actor)
            row = db.execute("SELECT * FROM documentation WHERE id = ?", (doc_id,)).fetchone()
            return _build_out(db, row)
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="Documentation with that title already exists")


@router.patch("/{doc_id}", response_model=DocumentationOut)
def update_doc(doc_id: int, req: DocumentationUpdate, request: Request):
    updates = {k: v for k, v in req.model_dump(exclude_unset=True).items() if k != "linked_entities"}
    now = datetime.now(timezone.utc).isoformat()
    actor = get_actor(request)
    with get_db() as db:
        if updates:
            updates["updated_at"] = now
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            values = list(updates.values()) + [doc_id]
            cursor = db.execute(f"UPDATE documentation SET {set_clause} WHERE id = ?", values)
            if cursor.rowcount == 0:
                raise HTTPException(status_code=404, detail="Documentation not found")
        if req.linked_entities is not None:
            _save_links(db, doc_id, req.linked_entities)
        log_event(db, "documentation", doc_id, None, "updated",
                  ", ".join(k for k in updates if k != "updated_at"), actor)
        row = db.execute("SELECT * FROM documentation WHERE id = ?", (doc_id,)).fetchone()
        return _build_out(db, row)


@router.delete("/{doc_id}")
def delete_doc(doc_id: int, request: Request):
    with get_db() as db:
        row = db.execute("SELECT id, title FROM documentation WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Documentation not found")
        db.execute("DELETE FROM documentation WHERE id = ?", (doc_id,))
        log_event(db, "documentation", doc_id, row["title"], "deleted", actor=get_actor(request))
    return {"status": "deleted", "id": doc_id}


@router.post("/{doc_id}/archive")
def archive_doc(doc_id: int, request: Request):
    with get_db() as db:
        row = db.execute("SELECT id, archived FROM documentation WHERE id = ?", (doc_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Documentation not found")
        new_val = 0 if row["archived"] else 1
        db.execute("UPDATE documentation SET archived = ?, updated_at = ? WHERE id = ?",
                   (new_val, datetime.now(timezone.utc).isoformat(), doc_id))
        action = "archived" if new_val else "unarchived"
        log_event(db, "documentation", doc_id, None, action, actor=get_actor(request))
    return {"status": action, "id": doc_id}
