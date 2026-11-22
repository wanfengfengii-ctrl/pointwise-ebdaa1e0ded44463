PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS source_batches (
    batch_id TEXT NOT NULL PRIMARY KEY,
    station TEXT NOT NULL,
    day TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    UNIQUE (batch_id, station)
);

CREATE TABLE IF NOT EXISTS source_rows (
    batch_id TEXT NOT NULL REFERENCES source_batches (batch_id),
    position INTEGER NOT NULL CHECK (position > 0),
    label TEXT NOT NULL,
    liters TEXT,
    PRIMARY KEY (batch_id, position)
);

CREATE TABLE IF NOT EXISTS editions (
    edition_id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    parent_id INTEGER,
    UNIQUE (edition_id, day),
    UNIQUE (edition_id, parent_id),
    FOREIGN KEY (parent_id, day) REFERENCES editions (edition_id, day)
);

CREATE TABLE IF NOT EXISTS edition_rows (
    edition_id INTEGER NOT NULL REFERENCES editions (edition_id),
    position INTEGER NOT NULL CHECK (position > 0),
    label TEXT NOT NULL,
    liters TEXT,
    station TEXT NOT NULL,
    source_batch TEXT NOT NULL,
    source_position INTEGER NOT NULL,
    PRIMARY KEY (edition_id, position),
    FOREIGN KEY (source_batch, station) REFERENCES source_batches (batch_id, station),
    FOREIGN KEY (source_batch, source_position) REFERENCES source_rows (batch_id, position)
);

CREATE TABLE IF NOT EXISTS edition_links (
    edition_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    parent_id INTEGER NOT NULL,
    parent_position INTEGER NOT NULL,
    PRIMARY KEY (edition_id, position),
    UNIQUE (edition_id, parent_position),
    FOREIGN KEY (edition_id, parent_id) REFERENCES editions (edition_id, parent_id),
    FOREIGN KEY (edition_id, position) REFERENCES edition_rows (edition_id, position),
    FOREIGN KEY (parent_id, parent_position) REFERENCES edition_rows (edition_id, position)
);

CREATE TABLE IF NOT EXISTS current_editions (
    day TEXT NOT NULL PRIMARY KEY,
    edition_id INTEGER NOT NULL,
    FOREIGN KEY (edition_id, day) REFERENCES editions (edition_id, day)
);

CREATE TABLE IF NOT EXISTS drafts (
    draft_id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    parent_id INTEGER,
    FOREIGN KEY (parent_id, day) REFERENCES editions (edition_id, day)
);

CREATE TABLE IF NOT EXISTS draft_inputs (
    draft_id INTEGER NOT NULL REFERENCES drafts (draft_id),
    position INTEGER NOT NULL CHECK (position > 0),
    batch_id TEXT NOT NULL REFERENCES source_batches (batch_id),
    PRIMARY KEY (draft_id, position),
    UNIQUE (draft_id, batch_id)
);

CREATE TABLE IF NOT EXISTS draft_rows (
    draft_id INTEGER NOT NULL REFERENCES drafts (draft_id),
    position INTEGER NOT NULL CHECK (position > 0),
    label TEXT NOT NULL,
    liters TEXT,
    station TEXT NOT NULL,
    source_batch TEXT NOT NULL,
    source_position INTEGER NOT NULL,
    parent_position INTEGER CHECK (parent_position > 0),
    PRIMARY KEY (draft_id, position),
    UNIQUE (draft_id, parent_position),
    FOREIGN KEY (source_batch, station) REFERENCES source_batches (batch_id, station),
    FOREIGN KEY (source_batch, source_position) REFERENCES source_rows (batch_id, position)
);

CREATE TABLE IF NOT EXISTS publication_receipts (
    request_id TEXT NOT NULL PRIMARY KEY,
    draft_id INTEGER NOT NULL REFERENCES drafts (draft_id),
    edition_id INTEGER NOT NULL UNIQUE REFERENCES editions (edition_id),
    token TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS export_sessions (
    session_id TEXT NOT NULL PRIMARY KEY,
    day TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS successful_pages (
    session_id TEXT NOT NULL REFERENCES export_sessions (session_id),
    page_no INTEGER NOT NULL CHECK (page_no > 0),
    page_size TEXT NOT NULL,
    edition_id INTEGER NOT NULL REFERENCES editions (edition_id),
    liters TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    PRIMARY KEY (session_id, page_no),
    UNIQUE (session_id, page_no, edition_id)
);

CREATE TABLE IF NOT EXISTS delivered_rows (
    session_id TEXT NOT NULL,
    page_no INTEGER NOT NULL,
    edition_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    PRIMARY KEY (session_id, page_no, position),
    FOREIGN KEY (session_id, page_no, edition_id)
        REFERENCES successful_pages (session_id, page_no, edition_id),
    FOREIGN KEY (edition_id, position) REFERENCES edition_rows (edition_id, position)
);

-- Merge-capable drafts are those fixed-parent drafts created after this schema
-- (and therefore the merge feature) was installed.  Drafts from older databases
-- stay absent and remain usable only through the ordinary single-draft routes.
CREATE TABLE IF NOT EXISTS merge_capable_drafts (
    draft_id INTEGER NOT NULL PRIMARY KEY REFERENCES drafts (draft_id)
);

-- One successful non-empty edit advances the revision, even if it restores the
-- previous contents.  Empty edit groups and rejected groups leave it alone.
CREATE TABLE IF NOT EXISTS draft_revisions (
    draft_id INTEGER NOT NULL PRIMARY KEY REFERENCES drafts (draft_id),
    revision INTEGER NOT NULL CHECK (revision > 0)
);

-- Original rows explicitly moved (by a non-trivial move) in a draft, keyed by
-- the shared parent position of the row that was moved at edit time.
CREATE TABLE IF NOT EXISTS draft_moved_originals (
    draft_id INTEGER NOT NULL REFERENCES drafts (draft_id),
    parent_position INTEGER NOT NULL CHECK (parent_position > 0),
    PRIMARY KEY (draft_id, parent_position)
);

CREATE TABLE IF NOT EXISTS merge_previews (
    preview_id INTEGER PRIMARY KEY,
    day TEXT NOT NULL,
    parent_id INTEGER NOT NULL,
    left_id INTEGER NOT NULL,
    right_id INTEGER NOT NULL,
    left_revision INTEGER NOT NULL,
    right_revision INTEGER NOT NULL,
    FOREIGN KEY (parent_id, day) REFERENCES editions (edition_id, day),
    FOREIGN KEY (left_id) REFERENCES drafts (draft_id),
    FOREIGN KEY (right_id) REFERENCES drafts (draft_id)
);

CREATE TABLE IF NOT EXISTS merge_preview_rows (
    preview_id INTEGER NOT NULL REFERENCES merge_previews (preview_id),
    position INTEGER NOT NULL CHECK (position > 0),
    label TEXT NOT NULL,
    liters TEXT,
    station TEXT NOT NULL,
    source_batch TEXT NOT NULL,
    source_position INTEGER NOT NULL,
    parent_position INTEGER CHECK (parent_position > 0),
    PRIMARY KEY (preview_id, position),
    FOREIGN KEY (source_batch, station) REFERENCES source_batches (batch_id, station),
    FOREIGN KEY (source_batch, source_position) REFERENCES source_rows (batch_id, position)
);

CREATE TABLE IF NOT EXISTS merge_receipts (
    request_id TEXT NOT NULL PRIMARY KEY,
    preview_id INTEGER NOT NULL REFERENCES merge_previews (preview_id),
    edition_id INTEGER NOT NULL UNIQUE REFERENCES editions (edition_id),
    token TEXT NOT NULL UNIQUE
);

COMMIT;
