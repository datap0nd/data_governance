"""Visibility helpers for archived-aware asset calculations."""


def get_active_source_ids(db) -> set[int]:
    """Return sources that should count toward live stats, alerts, and impact."""
    rows = db.execute(
        """
        WITH RECURSIVE active_sources(id) AS (
            SELECT DISTINCT s.id
            FROM sources s
            WHERE COALESCE(s.archived, 0) = 0
              AND s.discovered_by = 'manual'
              AND NOT EXISTS (
                  SELECT 1
                  FROM report_tables rt
                  WHERE rt.source_id = s.id
              )

            UNION

            SELECT DISTINCT s.id
            FROM sources s
            LEFT JOIN flows f
              ON f.sql_handoff_enabled=1 AND f.sql_target_source_id=s.id
            LEFT JOIN flow_file_source_bindings b
              ON b.source_id=s.id AND b.active=1
            WHERE COALESCE(s.archived, 0)=0
              AND (f.id IS NOT NULL OR b.id IS NOT NULL)

            UNION

            SELECT DISTINCT rt.source_id
            FROM report_tables rt
            JOIN reports r ON r.id = rt.report_id
            JOIN sources s ON s.id = rt.source_id
            WHERE rt.source_id IS NOT NULL
              AND COALESCE(r.archived, 0) = 0
              AND COALESCE(s.archived, 0) = 0

            UNION

            SELECT DISTINCT tl.entity_id
            FROM task_links tl
            JOIN tasks t ON t.id = tl.task_id
            JOIN sources s ON s.id = tl.entity_id
            WHERE tl.entity_type = 'source'
              AND t.status != 'done'
              AND COALESCE(s.archived, 0) = 0

            UNION

            SELECT sd.depends_on_id
            FROM source_dependencies sd
            JOIN active_sources a ON a.id = sd.source_id
            JOIN sources s ON s.id = sd.depends_on_id
            WHERE COALESCE(s.archived, 0) = 0
        )
        SELECT DISTINCT id FROM active_sources
        """
    ).fetchall()
    return {int(row["id"]) for row in rows}


def source_is_active(db, source_id: int) -> bool:
    return source_id in get_active_source_ids(db)
