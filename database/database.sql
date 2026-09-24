CREATE DATABASE IF NOT EXISTS weather_db;

USE weather_db;


CREATE TABLE IF NOT EXISTS users (

    user_id INT AUTO_INCREMENT PRIMARY KEY,

    full_name VARCHAR(100) NOT NULL,

    username VARCHAR(50) NOT NULL UNIQUE,

    email VARCHAR(150),

    department ENUM('IT', 'Data') NOT NULL,

    password_hash VARCHAR(255) NOT NULL,

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


CREATE TABLE IF NOT EXISTS stations (

    station_id INT AUTO_INCREMENT PRIMARY KEY,

    station_name VARCHAR(100) NOT NULL,

    latitude DECIMAL(9,6),

    longitude DECIMAL(9,6),

    status VARCHAR(30)

);


CREATE TABLE IF NOT EXISTS maintenance_records (

    maintenance_id INT AUTO_INCREMENT PRIMARY KEY,

    station_id INT NOT NULL,

    maintenance_date DATE NOT NULL,

    issue TEXT NOT NULL,

    activity_done TEXT NOT NULL,

    recommendations TEXT,

    technicians VARCHAR(255) NOT NULL,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT fk_maintenance_station
        FOREIGN KEY (station_id)
        REFERENCES stations(station_id)
        ON UPDATE CASCADE
        ON DELETE RESTRICT
);


CREATE TABLE IF NOT EXISTS instruments (

    instrument_id INT AUTO_INCREMENT PRIMARY KEY,

    instrument_name VARCHAR(100) NOT NULL UNIQUE,

    category VARCHAR(100),

    description VARCHAR(1000),

    is_active BOOLEAN NOT NULL DEFAULT TRUE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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


INSERT IGNORE INTO instruments (instrument_name, category, description)
VALUES
    ('Datalogger', 'Data acquisition', 'Records and stores station observations.'),
    ('Battery', 'Power', 'Provides backup power to station equipment.'),
    ('Solar Panel', 'Power', 'Charges the station battery.'),
    ('Rain Gauge', 'Precipitation', 'Measures rainfall amount.'),
    ('Temperature Sensor', 'Temperature', 'Measures air temperature.'),
    ('Humidity Sensor', 'Humidity', 'Measures relative humidity.'),
    ('Wind Sensor', 'Wind', 'Measures wind speed and direction.'),
    ('Barometer', 'Pressure', 'Measures atmospheric pressure.'),
    ('Radiation Shield', 'Housing', 'Protects sensors from direct radiation.'),
    ('Modem', 'Communication', 'Transmits observations to the data center.');


INSERT INTO stations
(
    station_name,
    latitude,
    longitude,
    status
)
VALUES
(
    'Kigali Aero',
    -1.9686,
    30.1395,
    'Operational'
);
