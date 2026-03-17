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
    case_id VARCHAR(255) NOT NULL,
    process_id INT NOT NULL REFERENCES process_definition(process_id) ON DELETE CASCADE,
    case_start TIMESTAMPTZ,
    case_end TIMESTAMPTZ,
    activity_count INT,
    variant TEXT,
    PRIMARY KEY (case_id, process_id)
);

CREATE TABLE process_map (
    map_id SERIAL PRIMARY KEY,
    process_id INT NOT NULL REFERENCES process_definition(process_id) ON DELETE CASCADE,
    map_name VARCHAR(255) NOT NULL DEFAULT 'default',
    source VARCHAR(50) NOT NULL DEFAULT 'manual',
    nodes JSONB NOT NULL,
    edges JSONB NOT NULL,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (process_id, map_name)
);

CREATE TABLE task_definition (
    task_id      SERIAL PRIMARY KEY,
    process_id   INT NOT NULL REFERENCES process_definition(process_id) ON DELETE CASCADE,
    task_name    VARCHAR(255) NOT NULL,
    description  TEXT,
    color        VARCHAR(7),
    created_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (process_id, task_name)
);

CREATE TABLE task_instance (
    task_instance_id  BIGSERIAL PRIMARY KEY,
    task_id           INT NOT NULL REFERENCES task_definition(task_id) ON DELETE CASCADE,
    case_id           VARCHAR(255) NOT NULL,
    process_id        INT NOT NULL REFERENCES process_definition(process_id) ON DELETE CASCADE,
    event_id_start    BIGINT NOT NULL REFERENCES event(event_id),
    event_id_end      BIGINT NOT NULL REFERENCES event(event_id),
    task_start        TIMESTAMPTZ NOT NULL,
    task_end          TIMESTAMPTZ NOT NULL,
    event_count       INT NOT NULL,
    created_at        TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE task_rule (
    rule_id      SERIAL PRIMARY KEY,
    task_id      INT NOT NULL REFERENCES task_definition(task_id) ON DELETE CASCADE,
    process_id   INT NOT NULL REFERENCES process_definition(process_id) ON DELETE CASCADE,
    rule_type    VARCHAR(50) NOT NULL DEFAULT 'sequence',
    rule_config  JSONB NOT NULL,
    priority     INT DEFAULT 0,
    is_active    BOOLEAN DEFAULT TRUE,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IX_event_case ON event (case_id, event_timestamp);
CREATE INDEX IX_event_activity ON event (activity_name);
CREATE INDEX IX_event_process ON event (process_id, event_timestamp);
CREATE INDEX IX_event_attrs ON event USING GIN (event_attrs);
CREATE INDEX IX_map_process ON process_map (process_id);
CREATE INDEX IX_task_instance_case ON task_instance (case_id, task_start);
CREATE INDEX IX_task_instance_process ON task_instance (process_id);
