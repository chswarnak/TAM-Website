-- TAM Smart Event Photo Sharing System
-- Database schema only; no application records
-- Generated from AWS SQLite database

-- table: admins
CREATE TABLE admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'admin',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        , deleted_at TEXT, must_change_password INTEGER NOT NULL DEFAULT 0);

-- table: attendee_consents
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

-- table: contributors
CREATE TABLE contributors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            event_id TEXT NOT NULL,
            approved INTEGER DEFAULT 0
        , mobile_number TEXT, uploads_enabled INTEGER NOT NULL DEFAULT 1, active INTEGER NOT NULL DEFAULT 1, deleted_at TEXT, password_hash TEXT, approved_by_admin_id INTEGER, approved_at TEXT);

-- table: events
CREATE TABLE events (
            event_id TEXT PRIMARY KEY,
            event_name TEXT NOT NULL,
            event_date TEXT,
            upload_start_time TEXT NOT NULL DEFAULT '17:00',
            upload_end_time TEXT NOT NULL DEFAULT '22:00',
            timezone TEXT NOT NULL DEFAULT 'America/Toronto',
            status TEXT NOT NULL DEFAULT 'draft',
            retention_hours INTEGER NOT NULL DEFAULT 48
        );

-- table: guest_consent_flows
CREATE TABLE guest_consent_flows (
                flow_hash TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                consent_id INTEGER NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                FOREIGN KEY (consent_id)
                    REFERENCES attendee_consents(id)
            );

-- table: guest_photo_downloads
CREATE TABLE guest_photo_downloads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            consent_id TEXT NOT NULL,
            photo_id INTEGER NOT NULL,
            downloaded_at_utc TEXT NOT NULL,
            session_token_hash TEXT NOT NULL
        );

-- table: guest_search_sessions
CREATE TABLE guest_search_sessions (
                token_hash TEXT PRIMARY KEY,
                event_id TEXT NOT NULL,
                consent_id TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL
            );

-- table: guest_session_photos
CREATE TABLE guest_session_photos (
                token_hash TEXT NOT NULL,
                photo_id INTEGER NOT NULL,
                PRIMARY KEY (token_hash, photo_id),
                FOREIGN KEY (token_hash)
                    REFERENCES guest_search_sessions(token_hash)
                    ON DELETE CASCADE
            );

-- table: organizer_permissions
CREATE TABLE organizer_permissions (
            admin_id INTEGER PRIMARY KEY,
            can_approve_contributors INTEGER NOT NULL DEFAULT 0,
            can_manage_photos INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (admin_id) REFERENCES admins(id)
        );

-- table: photo_face_authorizations
CREATE TABLE photo_face_authorizations (
            photo_id INTEGER PRIMARY KEY,
            authorized INTEGER NOT NULL DEFAULT 0
                CHECK (authorized IN (0, 1)),
            consent_reference TEXT,
            authorized_at TEXT,
            FOREIGN KEY (photo_id) REFERENCES photos(id)
        );

-- table: photo_faces
CREATE TABLE photo_faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            event_id TEXT NOT NULL,
            collection_id TEXT NOT NULL,
            rekognition_face_id TEXT NOT NULL UNIQUE,
            indexed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (photo_id) REFERENCES photos(id)
        );

-- table: photo_index_jobs
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

-- table: photos
CREATE TABLE photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            contributor_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            reviewed_by_admin_id INTEGER,
            reviewed_at TEXT, s3_bucket TEXT, s3_key TEXT,
            FOREIGN KEY (contributor_id)
                REFERENCES contributors(id)
        );

-- index: idx_attendee_consents_event
CREATE INDEX idx_attendee_consents_event
        ON attendee_consents(event_id, accepted_at);

-- index: idx_guest_consent_flows_expiry
CREATE INDEX idx_guest_consent_flows_expiry
            ON guest_consent_flows(expires_at);

-- index: idx_guest_sessions_expiry
CREATE INDEX idx_guest_sessions_expiry
            ON guest_search_sessions(expires_at);

-- index: idx_photo_faces_event
CREATE INDEX idx_photo_faces_event
        ON photo_faces(event_id, collection_id);

