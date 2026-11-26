-- TAM Smart Event Photo Sharing System
-- Production SQLite database schema
-- Exported: 20261126T180347Z
-- Schema only. No personal data or credentials.

PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;

-- TABLE: admin_contributor_links
CREATE TABLE admin_contributor_links (
                admin_id INTEGER PRIMARY KEY,
                contributor_id INTEGER NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

                FOREIGN KEY (admin_id)
                    REFERENCES admins(id),

                FOREIGN KEY (contributor_id)
                    REFERENCES contributors(id)
            );

-- TABLE: admins
CREATE TABLE admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        , deleted_at TEXT, must_change_password INTEGER NOT NULL DEFAULT 0, session_version INTEGER NOT NULL DEFAULT 1);

-- TABLE: attendee_consents
CREATE TABLE "attendee_consents" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            attendee_name TEXT NOT NULL,
            consent_type TEXT NOT NULL CHECK (
                consent_type IN (
                    'facial_indexing',
                    'selfie_search', 'gallery_access'
                )
            ),
            consent_version TEXT NOT NULL,
            consent_text TEXT NOT NULL,
            accepted_at TEXT NOT NULL,
            withdrawn_at TEXT,
            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP
        , attendee_mobile TEXT);

-- TABLE: contributor_consent_acknowledgements
CREATE TABLE contributor_consent_acknowledgements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            token_hash TEXT NOT NULL UNIQUE,

            consent_version TEXT NOT NULL,

            accepted_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            expires_at TEXT NOT NULL,

            revoked_at TEXT
        );

-- TABLE: contributor_registration_consents
CREATE TABLE contributor_registration_consents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,

                contributor_id INTEGER NOT NULL,

                event_id TEXT NOT NULL,

                consent_version TEXT NOT NULL,

                consent_text TEXT NOT NULL,

                accepted_at TEXT NOT NULL,

                withdrawn_at TEXT,

                FOREIGN KEY (contributor_id)
                    REFERENCES contributors(id)
            );

-- TABLE: contributor_registration_tokens
CREATE TABLE contributor_registration_tokens (
            token_hash TEXT PRIMARY KEY,
            contributor_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            revoked_at INTEGER,
            FOREIGN KEY (contributor_id)
                REFERENCES contributors(id)
        );

-- TABLE: contributor_rejection_audit
CREATE TABLE contributor_rejection_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            contributor_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            rejected_by_admin_id INTEGER NOT NULL,
            rejected_at TEXT NOT NULL,
            FOREIGN KEY (contributor_id)
                REFERENCES contributors(id),
            FOREIGN KEY (rejected_by_admin_id)
                REFERENCES admins(id)
        );

-- TABLE: contributors
CREATE TABLE contributors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            approved INTEGER DEFAULT 0
        , mobile_number TEXT, uploads_enabled INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, deleted_at TEXT, password_hash TEXT, approved_by_admin_id INTEGER, approved_at TEXT, email TEXT, username TEXT);

-- TABLE: events
CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            event_name TEXT NOT NULL,
            event_date TEXT,
            upload_start_time TEXT NOT NULL DEFAULT '17:00',
            upload_end_time TEXT NOT NULL DEFAULT '22:00',
            timezone TEXT NOT NULL DEFAULT 'America/Toronto',
            status TEXT NOT NULL DEFAULT 'draft',
            retention_hours INTEGER NOT NULL DEFAULT 48
        , gallery_enabled INTEGER NOT NULL DEFAULT 0, gallery_access_code_hash TEXT, gallery_code_required INTEGER NOT NULL DEFAULT 1 CHECK(gallery_code_required IN (0,1)));

-- TABLE: gallery_downloads
CREATE TABLE gallery_downloads (id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, media_type TEXT NOT NULL CHECK(media_type IN ("photo", "video")), media_id INTEGER NOT NULL, downloader_type TEXT NOT NULL CHECK(downloader_type IN ("contributor", "attendee", "admin")), downloader_id TEXT NOT NULL, downloaded_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);

-- TABLE: gallery_guest_sessions
CREATE TABLE gallery_guest_sessions (token_hash TEXT PRIMARY KEY, event_id TEXT NOT NULL, attendee_name TEXT NOT NULL, attendee_mobile TEXT NOT NULL, created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL);

-- TABLE: gallery_registration_attempts
CREATE TABLE gallery_registration_attempts (id INTEGER PRIMARY KEY AUTOINCREMENT, client_identifier TEXT NOT NULL, attempted_at INTEGER NOT NULL);

-- TABLE: guest_consent_flows
CREATE TABLE guest_consent_flows (
                flow_hash TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                consent_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY (consent_id)
                    REFERENCES attendee_consents(id)
            );

-- TABLE: guest_photo_downloads
CREATE TABLE guest_photo_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            consent_id TEXT NOT NULL,
            photo_id INTEGER NOT NULL,
            downloaded_at_utc TEXT NOT NULL,
            session_token_hash TEXT NOT NULL
        );

-- TABLE: guest_search_sessions
CREATE TABLE guest_search_sessions (
                token_hash TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                consent_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );

-- TABLE: guest_session_photos
CREATE TABLE guest_session_photos (
                token_hash TEXT NOT NULL,
                photo_id INTEGER NOT NULL,
                PRIMARY KEY (token_hash, photo_id),
                FOREIGN KEY (token_hash)
                    REFERENCES guest_search_sessions(token_hash)
                    ON DELETE CASCADE
            );

-- TABLE: organizer_permissions
CREATE TABLE organizer_permissions (
            admin_id INTEGER PRIMARY KEY,
            can_approve_contributors INTEGER NOT NULL DEFAULT 0,
            can_manage_photos INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (admin_id) REFERENCES admins(id)
        );

-- TABLE: photo_deletion_audit
CREATE TABLE photo_deletion_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                photo_id INTEGER NOT NULL,
                event_id TEXT NOT NULL,
                admin_id INTEGER NOT NULL,
                previous_status TEXT NOT NULL,
                deleted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

-- TABLE: photo_deletion_operations
CREATE TABLE photo_deletion_operations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            photo_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            original_contributor_id INTEGER NOT NULL,

            actor_type TEXT NOT NULL
                CHECK(actor_type IN ('admin', 'contributor')),

            actor_id INTEGER NOT NULL,

            deletion_source TEXT NOT NULL,

            requested_at_utc TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            completed_at_utc TEXT,

            cleanup_status TEXT NOT NULL DEFAULT 'pending'
                CHECK(cleanup_status IN (
                    'pending',
                    'processing',
                    'completed',
                    'failed'
                )),

            s3_cleanup_status TEXT NOT NULL DEFAULT 'pending',
            rekognition_cleanup_status TEXT NOT NULL DEFAULT 'pending',

            error_message TEXT,

            UNIQUE(photo_id)
        );

-- TABLE: photo_face_authorization_audit
CREATE TABLE photo_face_authorization_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    photo_id INTEGER NOT NULL,

    administrator_id INTEGER NOT NULL,

    decision TEXT NOT NULL
        CHECK (decision IN ('authorized', 'denied')),

    evidence_reference TEXT,

    decision_reason TEXT NOT NULL,

    decision_at TEXT NOT NULL
        DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (photo_id)
        REFERENCES photos(id),

    FOREIGN KEY (administrator_id)
        REFERENCES admins(id),

    CHECK (
        decision != 'authorized'
        OR (
            evidence_reference IS NOT NULL
            AND LENGTH(TRIM(evidence_reference)) > 0
        )
    )
);

-- TABLE: photo_face_authorizations
CREATE TABLE photo_face_authorizations (
            photo_id INTEGER PRIMARY KEY,
            authorized INTEGER NOT NULL DEFAULT 0
                CHECK (authorized IN (0, 1)),
            consent_reference TEXT,
            authorized_at TEXT,
            FOREIGN KEY (photo_id) REFERENCES photos(id)
        );

-- TABLE: photo_faces
CREATE TABLE photo_faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            rekognition_face_id TEXT NOT NULL UNIQUE,
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (photo_id) REFERENCES photos(id)
        );

-- TABLE: photo_index_jobs
CREATE TABLE photo_index_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            collection_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN (
                    'pending',
                    'processing',
                    'completed',
                    'failed'
                )),
            face_count INTEGER NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            error_message TEXT,
            started_at TEXT,
            completed_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(photo_id, collection_id),
            FOREIGN KEY(photo_id) REFERENCES photos(id)
        );

-- TABLE: photos
CREATE TABLE photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            contributor_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_by_admin_id INTEGER,
            reviewed_at TEXT, s3_bucket TEXT, s3_key TEXT, gallery_approved INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (contributor_id)
                REFERENCES contributors(id)
        );

-- TABLE: push_subscriptions
CREATE TABLE push_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            recipient_type TEXT NOT NULL
                CHECK (recipient_type IN ('admin', 'contributor')),

            recipient_id INTEGER NOT NULL,

            endpoint TEXT NOT NULL UNIQUE,
            p256dh TEXT NOT NULL,
            auth TEXT NOT NULL,

            created_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            updated_at TEXT NOT NULL
                DEFAULT CURRENT_TIMESTAMP,

            last_success_at TEXT,
            last_failure_at TEXT,

            active INTEGER NOT NULL DEFAULT 1
                CHECK (active IN (0, 1))
        );

-- TABLE: tv_display_state
CREATE TABLE tv_display_state (
    event_id TEXT PRIMARY KEY,

    display_mode TEXT NOT NULL DEFAULT 'auto'
        CHECK(display_mode IN (
            'auto',
            'slideshow',
            'photo_wall',
            'qr',
            'paused',
            'blank'
        )),

    current_batch_json TEXT NOT NULL DEFAULT '[]',

    batch_started_at_utc TEXT,
    batch_ends_at_utc TEXT,

    updated_by_admin_id INTEGER,

    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- TABLE: tv_event_settings
CREATE TABLE tv_event_settings (
    event_id TEXT PRIMARY KEY,

    enabled INTEGER NOT NULL DEFAULT 0
        CHECK(enabled IN (0,1)),

    max_photos_per_batch INTEGER NOT NULL DEFAULT 4
        CHECK(max_photos_per_batch BETWEEN 1 AND 4),

    batch_duration_seconds INTEGER NOT NULL DEFAULT 10
        CHECK(batch_duration_seconds = 10),

    slideshow_duration_seconds INTEGER NOT NULL DEFAULT 10
        CHECK(slideshow_duration_seconds BETWEEN 5 AND 60),

    ken_burns_enabled INTEGER NOT NULL DEFAULT 1
        CHECK(ken_burns_enabled IN (0,1)),

    auto_cycle_enabled INTEGER NOT NULL DEFAULT 1
        CHECK(auto_cycle_enabled IN (0,1)),

    qr_enabled INTEGER NOT NULL DEFAULT 1
        CHECK(qr_enabled IN (0,1)),

    quality_screening_enabled INTEGER NOT NULL DEFAULT 1
        CHECK(quality_screening_enabled IN (0,1)),

    updated_by_admin_id INTEGER,

    updated_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- TABLE: tv_photo_queue
CREATE TABLE tv_photo_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    event_id TEXT NOT NULL,
    photo_id INTEGER NOT NULL UNIQUE,

    projection_authorized INTEGER NOT NULL DEFAULT 0
        CHECK(projection_authorized IN (0,1)),

    quality_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(quality_status IN (
            'pending',
            'passed',
            'rejected',
            'manual_approved'
        )),

    queue_status TEXT NOT NULL DEFAULT 'pending'
        CHECK(queue_status IN (
            'pending',
            'ready',
            'displaying',
            'displayed',
            'skipped',
            'rejected'
        )),

    quality_score REAL,
    quality_reason TEXT,

    authorized_at_utc TEXT,
    queued_at_utc TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    displayed_at_utc TEXT,

    FOREIGN KEY(photo_id)
        REFERENCES photos(id)
        ON DELETE CASCADE
);

-- TABLE: tv_projection_policy
CREATE TABLE tv_projection_policy (
            event_id TEXT PRIMARY KEY,
            automatic_enabled INTEGER NOT NULL DEFAULT 0
                CHECK (automatic_enabled IN (0, 1)),
            consent_reference TEXT,
            updated_at_utc TEXT
        );

-- INDEX: contributors_event_email_unique
CREATE UNIQUE INDEX contributors_event_email_unique
            ON contributors(event_id, email COLLATE NOCASE)
            WHERE deleted_at IS NULL;

-- INDEX: contributors_event_username_unique
CREATE UNIQUE INDEX contributors_event_username_unique
            ON contributors(event_id, username COLLATE NOCASE)
            WHERE deleted_at IS NULL
              AND username IS NOT NULL;

-- INDEX: idx_attendee_consents_event
CREATE INDEX idx_attendee_consents_event
        ON attendee_consents(event_id, accepted_at);

-- INDEX: idx_contributor_consent_expiry
CREATE INDEX idx_contributor_consent_expiry
        ON contributor_consent_acknowledgements(expires_at);

-- INDEX: idx_contributor_registration_consents_lookup
CREATE INDEX idx_contributor_registration_consents_lookup
            ON contributor_registration_consents (
                contributor_id,
                event_id,
                accepted_at
            );

-- INDEX: idx_contributor_registration_tokens_contributor
CREATE INDEX idx_contributor_registration_tokens_contributor
        ON contributor_registration_tokens(contributor_id);

-- INDEX: idx_gallery_guest_sessions_expiry
CREATE INDEX idx_gallery_guest_sessions_expiry ON gallery_guest_sessions(expires_at);

-- INDEX: idx_gallery_registration_attempts_client_time
CREATE INDEX idx_gallery_registration_attempts_client_time ON gallery_registration_attempts(client_identifier, attempted_at);

-- INDEX: idx_guest_consent_flows_expiry
CREATE INDEX idx_guest_consent_flows_expiry
            ON guest_consent_flows(expires_at);

-- INDEX: idx_guest_sessions_expiry
CREATE INDEX idx_guest_sessions_expiry
            ON guest_search_sessions(expires_at);

-- INDEX: idx_photo_face_authorization_audit_admin
CREATE INDEX idx_photo_face_authorization_audit_admin
ON photo_face_authorization_audit(administrator_id);

-- INDEX: idx_photo_face_authorization_audit_photo
CREATE INDEX idx_photo_face_authorization_audit_photo
ON photo_face_authorization_audit(photo_id);

-- INDEX: idx_photo_faces_event
CREATE INDEX idx_photo_faces_event
        ON photo_faces(event_id, collection_id);

-- INDEX: idx_push_subscriptions_recipient
CREATE INDEX idx_push_subscriptions_recipient
        ON push_subscriptions (
            recipient_type,
            recipient_id,
            active
        );

-- INDEX: idx_tv_queue_event_status
CREATE INDEX idx_tv_queue_event_status
ON tv_photo_queue (
    event_id,
    queue_status,
    id
);

-- TRIGGER: tam_contributor_username_reserved
CREATE TRIGGER tam_contributor_username_reserved
            BEFORE INSERT ON contributors
            WHEN NEW.username IS NOT NULL
            BEGIN
                SELECT CASE WHEN EXISTS (
                    SELECT 1 FROM admins
                    WHERE LOWER(TRIM(username)) = LOWER(TRIM(NEW.username))
                )
                OR EXISTS (
                    SELECT 1 FROM contributors
                    WHERE event_id = NEW.event_id
                      AND username IS NULL
                      AND email IS NULL
                      AND deleted_at IS NULL
                      AND LOWER(TRIM(name)) = LOWER(TRIM(NEW.username))
                )
                THEN RAISE(ABORT, 'Username reserved')
                END;
            END;

PRAGMA user_version = 0;
PRAGMA application_id = 0;
COMMIT;
PRAGMA foreign_keys = ON;
