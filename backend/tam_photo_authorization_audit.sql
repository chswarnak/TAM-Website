-- TAM Facial Indexing Authorization Audit
-- SCHEMA PROPOSAL ONLY - DO NOT EXECUTE YET.
--
-- Administrator approval is not a substitute
-- for the affected individuals' valid consent.
--
-- Only the Super Admin should initially be permitted
-- to record biometric authorization decisions.

CREATE TABLE IF NOT EXISTS photo_face_authorization_audit (
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

CREATE INDEX IF NOT EXISTS
    idx_photo_face_authorization_audit_photo
ON photo_face_authorization_audit(photo_id);

CREATE INDEX IF NOT EXISTS
    idx_photo_face_authorization_audit_admin
ON photo_face_authorization_audit(administrator_id);
