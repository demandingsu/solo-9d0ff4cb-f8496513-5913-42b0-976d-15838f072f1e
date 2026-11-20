PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS archive (
    id              INTEGER PRIMARY KEY,
    name            TEXT    NOT NULL UNIQUE,
    source_filename TEXT    NOT NULL,
    layout_json     TEXT    NOT NULL,
    created_at      TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- One row per frame of a recorder CSV.
--
--   seq            : serial number of the frame as delivered by the recorder
--   sampled_at     : UTC sampling instant, normalized to ISO-8601 with 'Z'
--   sampled_at_raw : the same instant exactly as written in the source file,
--                    kept so the export reproduces the original CSV format
--   restored_mc    : the archived (restored) temperature in integer
--                    millidegrees Celsius -- authoritative
--   base_seq       : NULL when the frame is stored as an absolute value;
--                    otherwise the seq of the earlier frame it is relative to
--   kind_raw       : the kind token exactly as the source file spelled it,
--                    echoed back by the export (the kind itself never changes)
--   extras_json    : values of any other columns in the source file, echoed
--                    back verbatim by the export
--
-- The stored representation is derived, never duplicated:
--   absolute frame : stored = restored_mc
--   relative frame : stored = restored_mc - restored_mc(base_seq)
-- Deriving it is what lets a base frame be corrected without rewriting any
-- other frame: the others keep their restored temperature and their base
-- reference; only their derived difference moves.
CREATE TABLE IF NOT EXISTS frame (
    archive_id     INTEGER NOT NULL REFERENCES archive(id) ON DELETE CASCADE,
    seq            INTEGER NOT NULL,
    sampled_at     TEXT    NOT NULL,
    sampled_at_raw TEXT    NOT NULL,
    restored_mc    INTEGER NOT NULL,
    base_seq       INTEGER,
    kind_raw       TEXT    NOT NULL DEFAULT '',
    extras_json    TEXT    NOT NULL DEFAULT '[]',
    PRIMARY KEY (archive_id, seq),
    CHECK (base_seq IS NULL OR base_seq < seq),
    FOREIGN KEY (archive_id, base_seq) REFERENCES frame(archive_id, seq)
        DEFERRABLE INITIALLY DEFERRED
);

CREATE INDEX IF NOT EXISTS frame_by_base ON frame (archive_id, base_seq);

-- Append-only audit trail of accepted corrections.  Live state is always
-- frame.restored_mc / frame.base_seq; derived stored values are recorded here
-- so that each accepted edit shows what representation it produced.
CREATE TABLE IF NOT EXISTS revision (
    id               INTEGER PRIMARY KEY,
    archive_id       INTEGER NOT NULL REFERENCES archive(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,
    old_restored_mc  INTEGER NOT NULL,
    old_base_seq     INTEGER,
    old_stored_kind  TEXT    NOT NULL,
    old_stored_mc    INTEGER NOT NULL,
    new_restored_mc  INTEGER NOT NULL,
    new_base_seq     INTEGER,
    new_stored_kind  TEXT    NOT NULL,
    new_stored_mc    INTEGER NOT NULL,
    seen_restored_mc INTEGER NOT NULL,
    submitted_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS revision_by_frame ON revision (archive_id, seq, id);
