from fastapi import APIRouter
from app.database import get_db

router = APIRouter(prefix="/api/overview", tags=["overview"])


@router.get("/graph")
def get_overview_graph():
    """Full pipeline graph: all entities and their connections."""
    with get_db() as db:
        reports = db.execute("""
            SELECT r.id, r.name, r.owner FROM reports r
            WHERE COALESCE(r.archived, 0) = 0
        """).fetchall()

        sources = db.execute("""
            SELECT s.id, s.name, s.type, s.upstream_id,
                   sp.status
            FROM sources s
            LEFT JOIN (
                SELECT source_id, status,
                       ROW_NUMBER() OVER (PARTITION BY source_id ORDER BY probed_at DESC) AS rn
                FROM source_probes
            ) sp ON sp.source_id = s.id AND sp.rn = 1
            WHERE COALESCE(s.archived, 0) = 0
        """).fetchall()

        upstreams = db.execute("""
            SELECT id, name, code, refresh_day FROM upstream_systems
            WHERE COALESCE(archived, 0) = 0
        """).fetchall()

        scripts = db.execute("""
            SELECT id, display_name, hostname, machine_alias FROM scripts
            WHERE COALESCE(archived, 0) = 0
        """).fetchall()

        tasks = db.execute("""
            SELECT id, task_name, status, enabled, last_result,
                   schedule_type, script_id, machine_alias
            FROM scheduled_tasks
            WHERE COALESCE(archived, 0) = 0
        """).fetchall()

        # Edges: source -> report
        report_edges = db.execute("""
            SELECT DISTINCT source_id, report_id FROM report_tables
            WHERE source_id IS NOT NULL
        """).fetchall()

        # Edges: script -> source (writes)
        script_edges = db.execute("""
            SELECT DISTINCT script_id, source_id FROM script_tables
            WHERE direction = 'write' AND source_id IS NOT NULL
        """).fetchall()

        # Edges: source -> source (MV dependencies)
        source_deps = db.execute("""
            SELECT source_id, depends_on_id FROM source_dependencies
        """).fetchall()

    nodes = []
    for r in reports:
        nodes.append({
            "id": f"report-{r['id']}", "type": "report",
            "name": r["name"], "detail": r["owner"] or "",
        })
    for s in sources:
        nodes.append({
            "id": f"source-{s['id']}", "type": "source",
            "name": s["name"], "detail": s["type"] or "",
            "status": s["status"] or "unknown",
        })
    for u in upstreams:
        nodes.append({
            "id": f"upstream-{u['id']}", "type": "upstream",
            "name": u["name"], "detail": u["refresh_day"] or "",
        })
    for s in scripts:
        nodes.append({
            "id": f"script-{s['id']}", "type": "script",
            "name": s["display_name"],
            "detail": s["machine_alias"] or s["hostname"] or "",
        })
    for t in tasks:
        nodes.append({
            "id": f"task-{t['id']}", "type": "task",
            "name": t["task_name"],
            "detail": t["machine_alias"] or t["schedule_type"] or "",
        })

    edges = []
    for e in report_edges:
        edges.append({"source": f"source-{e['source_id']}", "target": f"report-{e['report_id']}"})
    for s in sources:
        if s["upstream_id"]:
            edges.append({"source": f"upstream-{s['upstream_id']}", "target": f"source-{s['id']}"})
    for e in script_edges:
        edges.append({"source": f"script-{e['script_id']}", "target": f"source-{e['source_id']}"})
    for t in tasks:
        if t["script_id"]:
            edges.append({"source": f"task-{t['id']}", "target": f"script-{t['script_id']}"})
    for d in source_deps:
        edges.append({"source": f"source-{d['depends_on_id']}", "target": f"source-{d['source_id']}"})

    return {"nodes": nodes, "edges": edges}
