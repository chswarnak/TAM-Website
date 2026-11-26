
"""TAM Live TV host control service."""

import sqlite3

EVENT_ID = "tam-diwali-2026"

ALLOWED_MODES = {
    "auto",
    "slideshow",
    "photo_wall",
    "paused",
    "blank",
}

ALLOWED_SETTINGS = {
    "enabled",
    "ken_burns_enabled",
    "auto_cycle_enabled",
    "quality_screening_enabled",
}


def get_host_status(db_path, event_id=EVENT_ID):

    with sqlite3.connect(db_path, timeout=15) as conn:

        conn.row_factory = sqlite3.Row

        settings = conn.execute(
            """
            SELECT *
            FROM tv_event_settings
            WHERE event_id = ?
            """,
            (event_id,)
        ).fetchone()

        state = conn.execute(
            """
            SELECT *
            FROM tv_display_state
            WHERE event_id = ?
            """,
            (event_id,)
        ).fetchone()

        if not settings or not state:
            raise ValueError("Live TV is not configured")

        rows = conn.execute(
            """
            SELECT q.queue_status, COUNT(*) AS total
            FROM tv_photo_queue q
            JOIN photos p ON p.id = q.photo_id
            WHERE q.event_id = ?
              AND p.event_id = q.event_id
              AND p.status = 'available'
            GROUP BY q.queue_status
            """,
            (event_id,)
        ).fetchall()

        queue = {
            key: 0 for key in (
                "pending",
                "ready",
                "displaying",
                "displayed",
                "skipped",
                "rejected",
            )
        }

        for row in rows:
            queue[row["queue_status"]] = row["total"]

        return {
            "settings": dict(settings),
            "state": dict(state),
            "queue": queue,
        }


def verify_host(conn, admin_id):

    if type(admin_id) is not int or admin_id <= 0:
        raise PermissionError("Invalid administrator")

    row = conn.execute(
        """
        SELECT id
        FROM admins
        WHERE id = ?
          AND role IN ('admin', 'super_admin')
          AND active = 1
          AND deleted_at IS NULL
          AND must_change_password = 0
        """,
        (admin_id,)
    ).fetchone()

    if not row:
        raise PermissionError("Administrator access denied")


def update_host_setting(
    db_path,
    admin_id,
    setting,
    value,
    event_id=EVENT_ID
):

    if setting not in ALLOWED_SETTINGS:
        raise ValueError("Unsupported Live TV setting")

    if type(value) is not int or value not in (0, 1):
        raise ValueError("Invalid setting value")

    with sqlite3.connect(db_path, timeout=15) as conn:

        conn.execute("BEGIN IMMEDIATE")

        verify_host(conn, admin_id)

        cursor = conn.execute(
            f"""
            UPDATE tv_event_settings
            SET {setting} = ?,
                updated_by_admin_id = ?,
                updated_at_utc = CURRENT_TIMESTAMP
            WHERE event_id = ?
            """,
            (value, admin_id, event_id)
        )

        if cursor.rowcount != 1:
            raise ValueError("Live TV settings unavailable")

        if (
            setting in ("enabled", "auto_cycle_enabled")
            and value == 0
        ):

            conn.execute(
                """
                UPDATE tv_photo_queue
                SET queue_status = 'ready'
                WHERE event_id = ?
                  AND queue_status = 'displaying'
                """,
                (event_id,)
            )

            conn.execute(
                """
                UPDATE tv_display_state
                SET current_batch_json = '[]',
                    batch_started_at_utc = NULL,
                    batch_ends_at_utc = NULL,
                    updated_by_admin_id = ?,
                    updated_at_utc = CURRENT_TIMESTAMP
                WHERE event_id = ?
                """,
                (admin_id, event_id)
            )

        conn.commit()

    return get_host_status(db_path, event_id)


def set_display_mode(
    db_path,
    admin_id,
    mode,
    event_id=EVENT_ID
):

    if mode not in ALLOWED_MODES:
        raise ValueError("Invalid display mode")

    with sqlite3.connect(db_path, timeout=15) as conn:

        conn.execute("BEGIN IMMEDIATE")

        verify_host(conn, admin_id)

        current = conn.execute(
            """
            SELECT display_mode
            FROM tv_display_state
            WHERE event_id = ?
            """,
            (event_id,)
        ).fetchone()

        if not current:
            raise ValueError("Live TV state unavailable")

        if current[0] != mode:

            conn.execute(
                """
                UPDATE tv_photo_queue
                SET queue_status = 'ready'
                WHERE event_id = ?
                  AND queue_status = 'displaying'
                """,
                (event_id,)
            )

            conn.execute(
                """
                UPDATE tv_display_state
                SET display_mode = ?,
                    current_batch_json = '[]',
                    batch_started_at_utc = NULL,
                    batch_ends_at_utc = NULL,
                    updated_by_admin_id = ?,
                    updated_at_utc = CURRENT_TIMESTAMP
                WHERE event_id = ?
                """,
                (mode, admin_id, event_id)
            )

        conn.commit()

    return get_host_status(db_path, event_id)
