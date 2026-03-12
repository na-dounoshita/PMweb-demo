CREATE TABLE process_definition (
    process_id SERIAL PRIMARY KEY,
    process_name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE event (
    event_id BIGSERIAL PRIMARY KEY,
    case_id VARCHAR(255) NOT NULL,
    activity_name VARCHAR(255) NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    process_id INT NOT NULL REFERENCES process_definition(process_id) ON DELETE CASCADE,
    source_system VARCHAR(100),
    event_attrs JSONB
);

CREATE TABLE case_instance (
    case_id VARCHAR(255) PRIMARY KEY,
    process_id INT NOT NULL REFERENCES process_definition(process_id) ON DELETE CASCADE,
    case_start TIMESTAMPTZ,
    case_end TIMESTAMPTZ,
    activity_count INT,
    variant TEXT
);

CREATE INDEX IX_event_case ON event (case_id, event_timestamp);
CREATE INDEX IX_event_activity ON event (activity_name);
CREATE INDEX IX_event_process ON event (process_id, event_timestamp);
CREATE INDEX IX_event_attrs ON event USING GIN (event_attrs);
