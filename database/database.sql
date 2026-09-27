CREATE DATABASE IF NOT EXISTS weather_db;

USE weather_db;


CREATE TABLE IF NOT EXISTS users (

    user_id INT AUTO_INCREMENT PRIMARY KEY,

    full_name VARCHAR(100) NOT NULL,

    username VARCHAR(50) NOT NULL UNIQUE,

    email VARCHAR(150),

    department ENUM(
        'Admin',
        'Observation Officer',
        'Observation Supervisor',
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
    reported_stations INT NOT NULL,
    validated_reports INT NOT NULL,
    notes TEXT,
    recorded_by_user_id INT,
    recorded_by_username VARCHAR(50),
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    CONSTRAINT fk_reporting_status_user FOREIGN KEY (recorded_by_user_id)
        REFERENCES users(user_id) ON DELETE SET NULL
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

    status VARCHAR(30) NOT NULL,

    suspended BOOLEAN NOT NULL DEFAULT FALSE,

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
