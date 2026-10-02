CREATE DATABASE IF NOT EXISTS Weather_Stations_App;

USE Weather_Stations_App;


CREATE TABLE IF NOT EXISTS users (

    user_id INT AUTO_INCREMENT PRIMARY KEY,

    full_name VARCHAR(100) NOT NULL,

    username VARCHAR(50) NOT NULL UNIQUE,

    email VARCHAR(150),

    department ENUM(
        'Admin',
        'Observation Officer',
        'Observation Supervisor',
        'Observation Supervisor at HQ',
        'Data Quality Control Officer',
        'Observation Processing Officer',
        'Big Data Specialist',
        'Data Quality Control Specialist',
        'Division Manager',
        'Instrument Maintenance and Calibration Officer'
    ) NOT NULL,

    password_hash VARCHAR(255) NOT NULL,

    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);


CREATE TABLE IF NOT EXISTS operation_activity (
    activity_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    actor_user_id INT NULL,
    actor_name VARCHAR(100) NOT NULL,
    action VARCHAR(20) NOT NULL,
    resource VARCHAR(100) NOT NULL,
    record_id VARCHAR(100) NULL,
    request_path VARCHAR(255) NOT NULL,
    affected_records INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_activity_created (created_at, activity_id)
);

CREATE TABLE IF NOT EXISTS record_edit_history (
    history_id BIGINT AUTO_INCREMENT PRIMARY KEY,
    entity_type VARCHAR(50) NOT NULL,
    entity_id VARCHAR(100) NOT NULL,
    before_data JSON NOT NULL,
    after_data JSON NOT NULL,
    changed_by_username VARCHAR(50) NOT NULL,
    changed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_record_edit_history (entity_type, entity_id, changed_at)
);

CREATE TABLE IF NOT EXISTS user_sessions (

    session_id INT AUTO_INCREMENT PRIMARY KEY,

    user_id INT NOT NULL,

    token_hash CHAR(64) NOT NULL UNIQUE,

    expires_at DATETIME NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_session_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS volunteer_data_files (
    file_id INT AUTO_INCREMENT PRIMARY KEY,
    report_month DATE NOT NULL,
    file_kind ENUM('monthly_qc', 'filtered_data', 'filled_data') NOT NULL,
    original_filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(120) NOT NULL,
    file_size INT NOT NULL,
    file_data LONGBLOB NOT NULL,
    uploaded_by_user_id INT,
    uploaded_by_username VARCHAR(50),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_volunteer_month_kind (report_month, file_kind),
    CONSTRAINT fk_volunteer_file_user FOREIGN KEY (uploaded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS volunteer_report_comments (
    comment_id INT AUTO_INCREMENT PRIMARY KEY,
    report_month DATE NOT NULL,
    comment TEXT NOT NULL,
    commented_by_user_id INT,
    commented_by_username VARCHAR(50),
    commented_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_volunteer_comment_user FOREIGN KEY (commented_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_volunteer_comment_month (report_month, commented_at)
);

CREATE TABLE IF NOT EXISTS monthly_reporting_status (
    reporting_status_id INT AUTO_INCREMENT PRIMARY KEY,
    report_month DATE NOT NULL UNIQUE,
    expected_stations INT NOT NULL,
    operational_stations INT NULL,
    under_maintenance_stations INT NULL,
    suspended_stations INT NULL,
    reported_stations INT NOT NULL,
    validated_reports INT NOT NULL,
    notes TEXT,
    recorded_by_user_id INT,
    recorded_by_username VARCHAR(50),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_reporting_status_user FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS monthly_reporting_changes (
    change_id INT AUTO_INCREMENT PRIMARY KEY,
    reporting_status_id INT NULL,
    report_month DATE NOT NULL,
    action ENUM('Created', 'Updated', 'Deleted', 'File uploaded') NOT NULL,
    before_data JSON NULL,
    after_data JSON NULL,
    changed_by_user_id INT NULL,
    changed_by_username VARCHAR(50) NOT NULL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_reporting_change_time (changed_at, change_id),
    INDEX idx_reporting_change_month (report_month, change_id),
    FOREIGN KEY (changed_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS monthly_non_reported_station_files (
    file_id INT AUTO_INCREMENT PRIMARY KEY,
    report_month DATE NOT NULL UNIQUE,
    original_filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    file_size INT NOT NULL,
    file_data LONGBLOB NOT NULL,
    uploaded_by_user_id INT,
    uploaded_by_username VARCHAR(50),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_non_reported_file_user FOREIGN KEY (uploaded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS monthly_data_requests (
    data_request_id INT AUTO_INCREMENT PRIMARY KEY,
    request_month DATE NOT NULL,
    category VARCHAR(100) NOT NULL,
    served_requests INT NOT NULL,
    notes VARCHAR(1000),
    recorded_by_user_id INT,
    recorded_by_username VARCHAR(50),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_request_month_category (request_month, category),
    CONSTRAINT fk_data_request_user FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    notification_id INT AUTO_INCREMENT PRIMARY KEY,
    user_id INT NOT NULL,
    notification_type VARCHAR(50) NOT NULL,
    title VARCHAR(150) NOT NULL,
    message VARCHAR(500) NOT NULL,
    related_record_type VARCHAR(50),
    related_record_id INT,
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_notification_user FOREIGN KEY (user_id)
        REFERENCES users(user_id) ON DELETE CASCADE,
    INDEX idx_notification_user_read (user_id, is_read, created_at)
);


CREATE TABLE IF NOT EXISTS stations (

    station_id INT AUTO_INCREMENT PRIMARY KEY,

    station_code VARCHAR(50) NOT NULL UNIQUE,

    station_name VARCHAR(100) NOT NULL,

    latitude DECIMAL(9,6) NOT NULL,

    longitude DECIMAL(9,6) NOT NULL,

    altitude DECIMAL(10,2) NOT NULL,

    province VARCHAR(100) NOT NULL,

    district VARCHAR(100) NOT NULL,

    sector VARCHAR(100) NOT NULL,

    station_category VARCHAR(60) NOT NULL,

    status ENUM('Operational', 'Under maintenance', 'Suspended', 'Closed') NOT NULL,

    comment TEXT,

    action TEXT,

    created_by_user_id INT,

    recorded_by_username VARCHAR(50),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_station_creator
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL

);


CREATE TABLE IF NOT EXISTS maintenance_records (

    maintenance_id INT AUTO_INCREMENT PRIMARY KEY,

    station_id INT NOT NULL,

    maintenance_date DATE NOT NULL,

    issue TEXT NOT NULL,

    activity_done TEXT NOT NULL,

    recommendations TEXT,

    technicians VARCHAR(255) NOT NULL,

    created_by_user_id INT,

    recorded_by_username VARCHAR(50),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_maintenance_station
        FOREIGN KEY (station_id)
        REFERENCES stations(station_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_maintenance_creator
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS maintenance_reports (
    report_id INT AUTO_INCREMENT PRIMARY KEY,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    notes VARCHAR(2000),
    original_filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    file_size INT NOT NULL,
    file_data LONGBLOB NOT NULL,
    uploaded_by_user_id INT,
    uploaded_by_username VARCHAR(50),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_maintenance_report_user FOREIGN KEY (uploaded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS maintenance_report_stations (
    report_id INT NOT NULL,
    station_id INT NOT NULL,
    PRIMARY KEY (report_id, station_id),
    CONSTRAINT fk_maintenance_report_station_report FOREIGN KEY (report_id)
        REFERENCES maintenance_reports(report_id) ON DELETE CASCADE,
    CONSTRAINT fk_maintenance_report_station_station FOREIGN KEY (station_id)
        REFERENCES stations(station_id) ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS pre_maintenance_reports (
    report_id INT AUTO_INCREMENT PRIMARY KEY,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    notes VARCHAR(2000),
    original_filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    file_size INT NOT NULL,
    file_data LONGBLOB NOT NULL,
    uploaded_by_user_id INT,
    uploaded_by_username VARCHAR(50),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_pre_maintenance_report_user FOREIGN KEY (uploaded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS pre_maintenance_report_stations (
    report_id INT NOT NULL,
    station_id INT NOT NULL,
    PRIMARY KEY (report_id, station_id),
    CONSTRAINT fk_pre_maintenance_report_station_report FOREIGN KEY (report_id)
        REFERENCES pre_maintenance_reports(report_id) ON DELETE CASCADE,
    CONSTRAINT fk_pre_maintenance_report_station_station FOREIGN KEY (station_id)
        REFERENCES stations(station_id) ON UPDATE CASCADE ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS station_maintenance_frequencies (
    station_id INT PRIMARY KEY,
    frequency_days INT NOT NULL DEFAULT 90,
    updated_by_user_id INT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (station_id) REFERENCES stations(station_id) ON DELETE CASCADE,
    FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS monthly_data_counts (
    data_count_id INT AUTO_INCREMENT PRIMARY KEY,
    station_id INT NOT NULL,
    record_month DATE NOT NULL,
    record_count INT NOT NULL,
    notes VARCHAR(1000) NULL,
    recorded_by_user_id INT NULL,
    recorded_by_username VARCHAR(50) NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_monthly_data_station_month (station_id, record_month),
    FOREIGN KEY (station_id) REFERENCES stations(station_id) ON DELETE RESTRICT,
    FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

-- Legacy station-level counts and day intervals remain for data preservation.
CREATE TABLE IF NOT EXISTS monthly_category_data_counts (
    data_count_id INT AUTO_INCREMENT PRIMARY KEY,
    station_category VARCHAR(60) NOT NULL,
    record_month DATE NOT NULL,
    record_count INT NOT NULL,
    notes VARCHAR(1000),
    recorded_by_user_id INT NULL,
    recorded_by_username VARCHAR(50),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_category_month (station_category, record_month),
    FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS monthly_combined_data_counts (
    data_count_id INT AUTO_INCREMENT PRIMARY KEY,
    category_key VARCHAR(500) NOT NULL,
    record_month DATE NOT NULL,
    record_count INT NOT NULL,
    notes VARCHAR(1000),
    recorded_by_user_id INT NULL,
    recorded_by_username VARCHAR(50),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_combined_month (record_month, category_key),
    FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS app_migration_markers (
    migration_key VARCHAR(100) PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS category_maintenance_targets (
    station_category VARCHAR(60) NOT NULL,
    fiscal_start_year SMALLINT NOT NULL,
    cadence ENUM('quarterly', 'yearly') NOT NULL,
    target_visits INT NOT NULL,
    updated_by_user_id INT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (station_category, fiscal_start_year),
    FOREIGN KEY (updated_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS visitor_categories (
    category_id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS station_visitors (
    visitor_id INT AUTO_INCREMENT PRIMARY KEY,
    station_id INT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    institution VARCHAR(200) NOT NULL DEFAULT '',
    mission VARCHAR(1000) NOT NULL,
    category_id INT NOT NULL,
    visitor_count INT NOT NULL,
    recorded_by_user_id INT NULL,
    recorded_by_username VARCHAR(50),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (station_id) REFERENCES stations(station_id) ON DELETE RESTRICT,
    FOREIGN KEY (category_id) REFERENCES visitor_categories(category_id) ON DELETE RESTRICT,
    FOREIGN KEY (recorded_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_station_visitors_period (period_start, station_id)
);

CREATE TABLE IF NOT EXISTS station_volunteers (
    volunteer_id INT AUTO_INCREMENT PRIMARY KEY,
    station_id INT NOT NULL,
    volunteer_name VARCHAR(150) NOT NULL,
    volunteer_identifier VARCHAR(50) NOT NULL,
    account_type VARCHAR(100) NOT NULL,
    account_name VARCHAR(150) NOT NULL,
    account_number VARCHAR(100) NOT NULL,
    mobile_phone VARCHAR(50) NOT NULL,
    recorded_by_user_id INT NULL,
    recorded_by_username VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_station_volunteer_station FOREIGN KEY (station_id)
        REFERENCES stations(station_id) ON DELETE RESTRICT,
    CONSTRAINT fk_station_volunteer_user FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL,
    UNIQUE KEY uq_station_volunteer_identifier (station_id, volunteer_identifier),
    INDEX idx_station_volunteer_name (volunteer_name)
);

CREATE TABLE IF NOT EXISTS station_inspections (
    inspection_id INT AUTO_INCREMENT PRIMARY KEY,
    station_id INT NOT NULL,
    inspection_date DATE NOT NULL,
    finding TEXT NOT NULL,
    workflow_stage VARCHAR(30) NOT NULL DEFAULT 'HQ Review',
    status VARCHAR(30) NOT NULL DEFAULT 'Open',
    not_solved_reason TEXT,
    created_by_user_id INT,
    created_by_username VARCHAR(50),
    updated_by_user_id INT,
    updated_by_username VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_station_inspection_station FOREIGN KEY (station_id)
        REFERENCES stations(station_id) ON UPDATE CASCADE ON DELETE RESTRICT,
    CONSTRAINT fk_station_inspection_creator FOREIGN KEY (created_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL,
    CONSTRAINT fk_station_inspection_updater FOREIGN KEY (updated_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_station_inspection_stage_status (workflow_stage, status, inspection_date)
);

CREATE TABLE IF NOT EXISTS station_inspection_comments (
    comment_id INT AUTO_INCREMENT PRIMARY KEY,
    inspection_id INT NOT NULL,
    action_type VARCHAR(40) NOT NULL DEFAULT 'Comment',
    comment TEXT,
    status_after VARCHAR(30),
    reason TEXT,
    commented_by_user_id INT,
    commented_by_username VARCHAR(50),
    commented_by_department VARCHAR(100),
    commented_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_station_inspection_comment_inspection FOREIGN KEY (inspection_id)
        REFERENCES station_inspections(inspection_id) ON DELETE CASCADE,
    CONSTRAINT fk_station_inspection_comment_user FOREIGN KEY (commented_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_station_inspection_comment_record (inspection_id, commented_at)
);

CREATE TABLE IF NOT EXISTS station_inspection_reports (
    report_id INT AUTO_INCREMENT PRIMARY KEY,
    inspection_id INT NULL,
    period_start DATE,
    period_end DATE,
    notes VARCHAR(2000),
    original_filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    file_size INT NOT NULL,
    file_data LONGBLOB NOT NULL,
    uploaded_by_user_id INT,
    uploaded_by_username VARCHAR(50),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_station_inspection_report_inspection FOREIGN KEY (inspection_id)
        REFERENCES station_inspections(inspection_id) ON DELETE CASCADE,
    CONSTRAINT fk_station_inspection_report_user FOREIGN KEY (uploaded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_station_inspection_report_record (inspection_id, uploaded_at)
);

CREATE TABLE IF NOT EXISTS station_inspection_report_stations (
    report_id INT NOT NULL,
    station_id INT NOT NULL,
    PRIMARY KEY (report_id, station_id),
    CONSTRAINT fk_inspection_report_station_report FOREIGN KEY (report_id)
        REFERENCES station_inspection_reports(report_id) ON DELETE CASCADE,
    CONSTRAINT fk_inspection_report_station_station FOREIGN KEY (station_id)
        REFERENCES stations(station_id) ON UPDATE CASCADE ON DELETE RESTRICT
);

INSERT IGNORE INTO station_inspection_report_stations (report_id, station_id)
SELECT reports.report_id, inspections.station_id
FROM station_inspection_reports AS reports
INNER JOIN station_inspections AS inspections
    ON inspections.inspection_id = reports.inspection_id;

CREATE TABLE IF NOT EXISTS station_inspection_photos (
    inspection_id INT PRIMARY KEY,
    original_filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    file_size INT NOT NULL,
    file_data LONGBLOB NOT NULL,
    uploaded_by_user_id INT,
    uploaded_by_username VARCHAR(50),
    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_inspection_photo_inspection FOREIGN KEY (inspection_id)
        REFERENCES station_inspections(inspection_id) ON DELETE CASCADE,
    CONSTRAINT fk_inspection_photo_user FOREIGN KEY (uploaded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS discussions (
    discussion_id INT AUTO_INCREMENT PRIMARY KEY,
    title VARCHAR(200) NOT NULL,
    status ENUM('Open', 'Closed') NOT NULL DEFAULT 'Open',
    created_by_user_id INT,
    created_by_username VARCHAR(50),
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    closed_by_user_id INT,
    closed_by_username VARCHAR(50),
    closed_at DATETIME,
    CONSTRAINT fk_discussion_creator FOREIGN KEY (created_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL,
    CONSTRAINT fk_discussion_closer FOREIGN KEY (closed_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_discussion_status_created (status, created_at)
);

CREATE TABLE IF NOT EXISTS discussion_participants (
    discussion_id INT NOT NULL,
    user_id INT NOT NULL,
    invited_by_user_id INT,
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (discussion_id, user_id),
    CONSTRAINT fk_discussion_participant_discussion FOREIGN KEY (discussion_id)
        REFERENCES discussions(discussion_id) ON DELETE CASCADE,
    CONSTRAINT fk_discussion_participant_user FOREIGN KEY (user_id)
        REFERENCES users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_discussion_participant_inviter FOREIGN KEY (invited_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS discussion_messages (
    message_id INT AUTO_INCREMENT PRIMARY KEY,
    discussion_id INT NOT NULL,
    message TEXT NOT NULL,
    posted_by_user_id INT,
    posted_by_username VARCHAR(50),
    posted_by_full_name VARCHAR(100),
    posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_discussion_message_discussion FOREIGN KEY (discussion_id)
        REFERENCES discussions(discussion_id) ON DELETE CASCADE,
    CONSTRAINT fk_discussion_message_user FOREIGN KEY (posted_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL,
    INDEX idx_discussion_message_thread (discussion_id, posted_at)
);


CREATE TABLE IF NOT EXISTS user_station_assignments (

    user_id INT NOT NULL,

    station_id INT NOT NULL,

    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (user_id, station_id),

    CONSTRAINT fk_assignment_user
        FOREIGN KEY (user_id)
        REFERENCES users(user_id)
        ON DELETE CASCADE,

    CONSTRAINT fk_assignment_station
        FOREIGN KEY (station_id)
        REFERENCES stations(station_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS instruments (

    instrument_id INT AUTO_INCREMENT PRIMARY KEY,

    instrument_name VARCHAR(100) NOT NULL UNIQUE,

    parameters_taken VARCHAR(1000) NOT NULL,

    category VARCHAR(100),

    description VARCHAR(1000),

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_by_user_id INT,

    recorded_by_username VARCHAR(50),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_instrument_creator
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS maintenance_record_instruments (

    maintenance_id INT NOT NULL,

    instrument_id INT NOT NULL,

    issue TEXT,

    action_done TEXT,

    recommendation TEXT,

    PRIMARY KEY (maintenance_id, instrument_id),

    CONSTRAINT fk_record_instrument_maintenance
        FOREIGN KEY (maintenance_id)
        REFERENCES maintenance_records(maintenance_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE,

    CONSTRAINT fk_record_instrument_instrument
        FOREIGN KEY (instrument_id)
        REFERENCES instruments(instrument_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);


CREATE TABLE IF NOT EXISTS instrument_station_categories (

    instrument_id INT NOT NULL,

    station_category VARCHAR(60) NOT NULL,

    PRIMARY KEY (instrument_id, station_category),

    CONSTRAINT fk_instrument_station_category
        FOREIGN KEY (instrument_id)
        REFERENCES instruments(instrument_id)
        ON UPDATE CASCADE
        ON DELETE CASCADE
);


CREATE TABLE IF NOT EXISTS suspected_data_records (

    suspected_data_id INT AUTO_INCREMENT PRIMARY KEY,

    station_id INT NOT NULL,

    issue VARCHAR(255) NOT NULL,

    description TEXT NOT NULL,

    reported_by_user_id INT,

    reported_by_username VARCHAR(50),

    maintenance_date DATE,

    maintenance_issue TEXT,

    how_solved TEXT,

    maintenance_outcome VARCHAR(30),

    way_forward TEXT,

    resolved_by_user_id INT,

    resolved_by_username VARCHAR(50),

    resolved_at DATETIME,

    status VARCHAR(30) NOT NULL DEFAULT 'Open',

    final_is_solved BOOLEAN,

    final_comment TEXT,

    final_reviewed_by_user_id INT,

    final_reviewed_by_username VARCHAR(50),

    final_reviewed_at DATETIME,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_suspected_data_station
        FOREIGN KEY (station_id)
        REFERENCES stations(station_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_suspected_data_reporter
        FOREIGN KEY (reported_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL,

    CONSTRAINT fk_suspected_data_resolver
        FOREIGN KEY (resolved_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL,

    CONSTRAINT fk_suspected_data_final_reviewer
        FOREIGN KEY (final_reviewed_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS station_instruments (

    station_instrument_id INT AUTO_INCREMENT PRIMARY KEY,

    station_id INT NOT NULL,

    instrument_id INT NOT NULL,

    model VARCHAR(100),

    manufacturer VARCHAR(100),

    serial_number VARCHAR(100),

    installation_date DATE NOT NULL,

    calibration_replacement_date DATE NULL,

    calibration_date DATE NULL,

    replacement_date DATE NULL,

    recommended_calibration_date DATE NULL,

    recommended_replacement_date DATE NULL,

    status VARCHAR(30) NOT NULL,

    comment TEXT,

    created_by_user_id INT,

    recorded_by_username VARCHAR(50),

    updated_by_user_id INT,

    updated_by_username VARCHAR(50),

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_station_instrument_station
        FOREIGN KEY (station_id)
        REFERENCES stations(station_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_station_instrument_catalog
        FOREIGN KEY (instrument_id)
        REFERENCES instruments(instrument_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT,

    CONSTRAINT fk_station_instrument_creator
        FOREIGN KEY (created_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL,

    CONSTRAINT fk_station_instrument_updater
        FOREIGN KEY (updated_by_user_id)
        REFERENCES users(user_id)
        ON DELETE SET NULL
);


CREATE TABLE IF NOT EXISTS instrument_alert_deliveries (
    station_instrument_id INT NOT NULL,
    user_id INT NOT NULL,
    alert_kind VARCHAR(20) NOT NULL,
    alert_month DATE NOT NULL,
    PRIMARY KEY (station_instrument_id, user_id, alert_kind, alert_month),
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

INSERT INTO stations
(
    station_code,
    station_name,
    latitude,
    longitude,
    altitude,
    province,
    district,
    sector,
    station_category,
    status,
    comment
)
VALUES
(
    'KGL-AERO',
    'Kigali Aero',
    -1.9686,
    30.1395,
    1491,
    'Kigali City',
    'Kicukiro',
    'Kanombe',
    'Automatic Weather stations',
    'Operational',
    'Airport weather station'
);
