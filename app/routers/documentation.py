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
def list_docs(include_archived: bool = Query(False), report_id: int | None = Query(None)):
    with get_db() as db:
        conditions = []
        params = []
        if not include_archived:
            conditions.append("archived = 0")
        if report_id is not None:
            conditions.append("report_id = ?")
            params.append(report_id)
        filt = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = db.execute(f"SELECT * FROM documentation {filt} ORDER BY title", params).fetchall()
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


@router.post("/ai-suggest/{report_id}")
def ai_suggest_doc(report_id: int):
    """Use AI to generate documentation from structured report context."""
    import logging
    from app.config import AI_MOCK

    log = logging.getLogger(__name__)

    with get_db() as db:
        report = db.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")

        # Gather context: tables, sources, measures, scripts, schedules
        tables = db.execute("""
            SELECT rt.table_name, s.name AS source_name, s.type AS source_type,
                   us.name AS upstream_name
            FROM report_tables rt
            LEFT JOIN sources s ON s.id = rt.source_id
            LEFT JOIN upstream_systems us ON us.id = s.upstream_id
            WHERE rt.report_id = ?
            ORDER BY rt.table_name
        """, (report_id,)).fetchall()

        measures = db.execute("""
            SELECT measure_name, table_name, measure_dax
            FROM report_measures WHERE report_id = ?
            ORDER BY table_name, measure_name LIMIT 30
        """, (report_id,)).fetchall()

        source_ids = [t["source_id"] for t in db.execute(
            "SELECT DISTINCT source_id FROM report_tables WHERE report_id = ? AND source_id IS NOT NULL",
            (report_id,)
        ).fetchall()]

        scripts = []
        sched_tasks = []
        if source_ids:
            ph = ",".join("?" * len(source_ids))
            scripts = db.execute(f"""
                SELECT DISTINCT sc.display_name, st.direction
                FROM script_tables st JOIN scripts sc ON sc.id = st.script_id
                WHERE st.source_id IN ({ph})
            """, source_ids).fetchall()

            script_ids = [s["id"] for s in db.execute(f"""
                SELECT DISTINCT sc.id FROM script_tables st
                JOIN scripts sc ON sc.id = st.script_id
                WHERE st.source_id IN ({ph})
            """, source_ids).fetchall()]
            if script_ids:
                ph2 = ",".join("?" * len(script_ids))
                sched_tasks = db.execute(f"""
                    SELECT task_name, schedule_type FROM scheduled_tasks
                    WHERE script_id IN ({ph2}) AND archived = 0
                """, script_ids).fetchall()

        # Report pages and visuals
        pages = db.execute(
            "SELECT rp.page_name, rp.page_ordinal, rp.id AS page_id "
            "FROM report_pages rp WHERE rp.report_id = ? ORDER BY rp.page_ordinal",
            (report_id,)
        ).fetchall()

        pages_info = []
        for page in pages:
            visuals = db.execute(
                "SELECT rv.visual_type, rv.title FROM report_visuals rv "
                "WHERE rv.page_id = ? ORDER BY rv.id",
                (page["page_id"],)
            ).fetchall()
            # Count visuals by type
            type_counts = {}
            for v in visuals:
                vtype = v["visual_type"] or "Unknown"
                type_counts[vtype] = type_counts.get(vtype, 0) + 1
            type_summary = ", ".join(f"{count} {vtype}" for vtype, count in type_counts.items())
            pages_info.append(
                f"Page {page['page_ordinal']}: {page['page_name']} - {type_summary}"
                if type_summary else f"Page {page['page_ordinal']}: {page['page_name']}"
            )

    # Build structured context for the prompt
    source_types = {}
    for t in tables:
        stype = t["source_type"] or "unknown"
        if stype not in source_types:
            source_types[stype] = []
        source_types[stype].append(t["source_name"] or t["table_name"])

    sources_text = ", ".join(f"{k}: {len(v)} ({', '.join(v)})" for k, v in source_types.items())

    # Send measure NAMES only - the small model parrots raw DAX back if we include it
    measure_details = []
    for m in measures[:25]:
        measure_details.append(f"- {m['measure_name']}")

    script_names = [s["display_name"] for s in scripts]
    schedule_names = [s["task_name"] for s in sched_tasks]

    context = (
        f"Report: {report['name']}\n"
        f"Owner: {report['owner'] or 'Unknown'}\n"
        f"Business Owner: {report['business_owner'] or 'Unknown'}\n"
        f"Frequency: {report['frequency'] or 'Unknown'}\n"
        f"\nPages and visuals:\n" + ("\n".join(pages_info) if pages_info else "No page data available") + "\n"
        f"\nData sources ({len(tables)} tables): {sources_text}\n"
        f"Upstream systems: {', '.join(set(t['upstream_name'] for t in tables if t['upstream_name'])) or 'None detected'}\n"
        f"\nMeasures ({len(measures)}):\n" + "\n".join(measure_details) + "\n"
        f"\nETL scripts: {', '.join(script_names) if script_names else 'None detected'}\n"
        f"Scheduled tasks: {', '.join(schedule_names) if schedule_names else 'None detected'}\n"
    )

    system_prompt = (
        "You document Power BI reports for a regional analytics team. "
        "These reports track sales performance, profitability, market share, "
        "and operational metrics.\n\n"
        "Write documentation that helps someone understand what a report does "
        "in under 60 seconds. Be specific - use actual metric names from the context.\n\n"
        "Rules:\n"
        "- purpose: 2-3 sentences. State what business questions the report answers. "
        "Reference specific metrics visible in the report (e.g. \"Tracks net sales, "
        "gross margin, and operating profit across subsidiaries\").\n"
        "- formulas: One line per key measure, separated by newlines. "
        "Format each line as: \"Metric Name = simple definition\". "
        "Example: \"Gross Margin = Gross Sales minus Cost of Goods Sold\". "
        "Example: \"SD% = Sales Deduction divided by Gross Sales\". "
        "Example: \"vs LY = Current year value divided by last year value\". "
        "NEVER reference DAX, table names, column filters, or P2 codes. "
        "Max 15 measures.\n\n"
        "Respond with valid JSON only. No markdown fences. "
        "Keys: purpose, formulas"
    )

    if AI_MOCK:
        # Return a structured placeholder when AI is not configured
        return {
            "purpose": f"[AI not configured] Report '{report['name']}' uses {len(tables)} data tables from {sources_text}.",
            "formulas": "\n".join(f"- {m['measure_name']}: [needs AI to explain]" for m in measures[:10]),
            "context_preview": context,
        }

    try:
        from app.ai.llm_provider import call_llm
        raw = call_llm(system_prompt, context)
        # Try to parse as JSON
        raw = raw.strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        result = json.loads(raw)
        return {
            "purpose": result.get("purpose"),
            "formulas": result.get("formulas"),
        }
    except json.JSONDecodeError:
        log.warning("AI returned non-JSON, using raw text as purpose")
        return {
            "purpose": raw[:500] if raw else None,
            "formulas": None,
        }
    except Exception as e:
        log.exception("AI suggest failed: %s", e)
        raise HTTPException(status_code=502, detail=f"AI service error: {e}")


@router.post("/ai-suggest-all")
def ai_suggest_all(request: Request):
    """Batch-generate AI documentation for all reports that don't have complete docs."""
    import logging
    from app.config import AI_MOCK

    log = logging.getLogger(__name__)
    if AI_MOCK:
        raise HTTPException(status_code=503, detail="AI is not configured. Set DG_AI_API_URL and DG_AI_API_KEY.")

    actor = get_actor(request)
    results = {"generated": 0, "skipped": 0, "failed": 0, "errors": []}

    with get_db() as db:
        reports = db.execute("SELECT id, name FROM reports WHERE archived = 0 ORDER BY name").fetchall()

    for report_row in reports:
        report_id = report_row["id"]
        try:
            # Check existing doc completeness
            with get_db() as db:
                existing = db.execute(
                    "SELECT * FROM documentation WHERE report_id = ?", (report_id,)
                ).fetchone()

            if existing:
                fields = [existing["business_purpose"], existing["technical_transformations"]]
                filled = sum(1 for f in fields if f and str(f).strip())
                if filled >= 2:
                    results["skipped"] += 1
                    continue

            # Call the single-report AI suggest
            suggestion = ai_suggest_doc(report_id)

            # Save to database
            now = datetime.now(timezone.utc).isoformat()
            with get_db() as db:
                if existing:
                    # Only fill empty fields
                    updates = {}
                    if not existing["business_purpose"] and suggestion.get("purpose"):
                        updates["business_purpose"] = suggestion["purpose"]
                    if not existing["technical_transformations"] and suggestion.get("formulas"):
                        updates["technical_transformations"] = suggestion["formulas"]
                    if updates:
                        updates["updated_at"] = now
                        set_clause = ", ".join(f"{k} = ?" for k in updates)
                        db.execute(
                            f"UPDATE documentation SET {set_clause} WHERE id = ?",
                            list(updates.values()) + [existing["id"]],
                        )
                        log_event(db, "documentation", existing["id"], report_row["name"],
                                  "ai-generated", actor=actor)
                else:
                    cursor = db.execute(
                        """INSERT INTO documentation
                           (report_id, title, business_purpose, technical_transformations,
                            status, created_by, created_at, updated_at)
                           VALUES (?, ?, ?, ?, 'draft', ?, ?, ?)""",
                        (report_id, report_row["name"],
                         suggestion.get("purpose"), suggestion.get("formulas"),
                         actor, now, now),
                    )
                    doc_id = cursor.lastrowid
                    db.execute(
                        "INSERT OR IGNORE INTO doc_entity_links (doc_id, entity_type, entity_id) VALUES (?, 'report', ?)",
                        (doc_id, report_id),
                    )
                    log_event(db, "documentation", doc_id, report_row["name"],
                              "ai-generated", actor=actor)

            results["generated"] += 1
        except Exception as e:
            log.warning("AI suggest failed for report %s: %s", report_row["name"], e)
            results["failed"] += 1
            results["errors"].append(f"{report_row['name']}: {str(e)[:100]}")

    return results


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
