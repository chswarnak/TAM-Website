-- TAM Smart Event Photo Sharing System
-- Database schema only; no attendee or Contributor data.
-- Event: tam-diwali-2026
-- Generated from the production SQLite schema.

PRAGMA foreign_keys = ON;

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
CREATE TABLE attendee_consents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            attendee_name TEXT NOT NULL,
            consent_type TEXT NOT NULL CHECK (
                consent_type IN (
                    'facial_indexing',
                    'selfie_search'
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
        , mobile_number TEXT, uploads_enabled INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, deleted_at TEXT, password_hash TEXT, approved_by_admin_id INTEGER, approved_at TEXT, email TEXT);

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
        , gallery_enabled INTEGER NOT NULL DEFAULT 0, gallery_access_code_hash TEXT);

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

-- INDEX: contributors_event_email_unique
CREATE UNIQUE INDEX contributors_event_email_unique
            ON contributors(event_id, email COLLATE NOCASE)
            WHERE deleted_at IS NULL
        ;

-- INDEX: idx_attendee_consents_event
CREATE INDEX idx_attendee_consents_event
        ON attendee_consents(event_id, accepted_at)
    ;

-- INDEX: idx_contributor_consent_expiry
CREATE INDEX idx_contributor_consent_expiry
        ON contributor_consent_acknowledgements(expires_at)
    ;

-- INDEX: idx_contributor_registration_tokens_contributor
CREATE INDEX idx_contributor_registration_tokens_contributor
        ON contributor_registration_tokens(contributor_id)
    ;

-- INDEX: idx_gallery_guest_sessions_expiry
CREATE INDEX idx_gallery_guest_sessions_expiry ON gallery_guest_sessions(expires_at);

-- INDEX: idx_gallery_registration_attempts_client_time
CREATE INDEX idx_gallery_registration_attempts_client_time ON gallery_registration_attempts(client_identifier, attempted_at);

-- INDEX: idx_guest_consent_flows_expiry
CREATE INDEX idx_guest_consent_flows_expiry
            ON guest_consent_flows(expires_at)
        ;

-- INDEX: idx_guest_sessions_expiry
CREATE INDEX idx_guest_sessions_expiry
            ON guest_search_sessions(expires_at)
        ;

-- INDEX: idx_photo_face_authorization_audit_admin
CREATE INDEX idx_photo_face_authorization_audit_admin
ON photo_face_authorization_audit(administrator_id);

-- INDEX: idx_photo_face_authorization_audit_photo
CREATE INDEX idx_photo_face_authorization_audit_photo
ON photo_face_authorization_audit(photo_id);

-- INDEX: idx_photo_faces_event
CREATE INDEX idx_photo_faces_event
        ON photo_faces(event_id, collection_id)
    ;

-- INDEX: idx_push_subscriptions_recipient
CREATE INDEX idx_push_subscriptions_recipient
        ON push_subscriptions (
            recipient_type,
            recipient_id,
            active
        )
    ;
