START TRANSACTION;

UPDATE stations
SET station_category = CASE
    WHEN station_name = 'Maranyundo_WR' THEN 'Weather radar'
    WHEN station_name = 'Butare Aero_UAS' THEN 'Upper air station'
    WHEN RIGHT(TRIM(station_name), 4) = '_AWS' THEN 'Automatic Weather stations'
    WHEN RIGHT(TRIM(station_name), 4) = '_ARG' THEN 'Automatic Raingauge'
    WHEN station_name IN (
        'Kawangire', 'Kazo', 'Nyagatare', 'Gitega', 'Byumba',
        'Busogo  ISAE', 'Gikongoro', 'Byimana', 'Rubengera',
        'Kigali  Airport', 'Ruhengeri Aero', 'Gisenyi Aero', 'Kamembe Aero'
    ) THEN 'Principal stations'
    WHEN station_name IN (
        'Juru', 'Nyamata_Paroisse', 'Kabarore', 'Ngarama',
        'Nyagahanga_EFA', 'Mukarange', 'Rwinkwavu', 'Gahara', 'Kirehe',
        'Mpanga', 'Jarama', 'Zaza', 'Gatunda', 'Karangazi', 'Rwamagana',
        'Musha', 'Butamwa', 'Kagogo', 'Ntaruka', 'Ruhunde', 'Janja',
        'Ruli', 'Rushashi', 'Mulindi_Usine', 'Kinigi', 'Remera',
        'Cyinzuzi', 'Mugambazi', 'Cyili_Rice Cooperative', 'Kansi',
        'Kigembe', 'Nyakibanda_Grand Seminaire', 'Rubona', 'Gacurabwenge',
        'Kayenzi', 'Mugina', 'Kibangu', 'Nyamabuye',
        'GS Notre Dame de la Paix Cyanika', 'GS Kigeme A', 'Kaduha',
        'Kitabi Tea', 'Mushubi_Paroisse', 'Busasamana_Nyanza', 'Runyombyi',
        'Kinazi', 'Twumba', 'Muramba', 'Nyange_Ngororero', 'Sovu',
        'Bigogwe', 'Cyato', 'Nyamasheke', 'Ntendezi', 'Kanama', 'Bugarama',
        'Gihango', 'Murunda_Paroisse'
    ) THEN 'Climatic stations'
    WHEN station_name IN (
        'Ruhuha', 'Gasange', 'Rwimbogo', 'Kiziguro', 'Muhura', 'Gahini',
        'Kabarondo', 'Murundi', 'Mwiri', 'Ndego', 'Musaza', 'Nyamugali',
        'Nyarubuye_Paroisse', 'Mutenderi', 'Sake', 'Karama_Nyagatare',
        'Kagitumba', 'Rwempasha', 'Gishali', 'Mwulire', 'Nzige', 'Rubungo',
        'Butaro', 'Kinoni_Paroisse', 'Cyabingo', 'Minazi', 'Muyongwe',
        'Nemba_Paroisse', 'Rwesero', 'Nyamiyaga _Gicumbi', 'Nyange_Musanze',
        'Shingiro', 'Rutongo_Paroisse', 'Rukozo', 'Rusiga', 'Gikonko',
        'Gakoma_Paroisse', 'Mukindo', 'Save', 'Karama_Huye', 'Kigoma_Huye',
        'Simbi', 'Kivumu_Paroisse', 'Nyabinoni', 'Rugendabari', 'Buruhukiro',
        'Gatare', 'Tare', 'Nyamiyaga_Paroisse', 'Rurangazi', 'Cyahinda',
        'Kibeho', 'Gitwe', 'Mwendo', 'Kigoma_Ruhango', 'Gishyita',
        'Rugabano', 'Gatumba', 'Kabaya', 'Muhanda', 'Rwankeri', 'Rugera',
        'Kanjongo', 'Kirimbi', 'Shangi_Paroisse', 'Busasamana', 'Cyanzarwe',
        'Nyundo_Paroisse', 'Mibirizi Paroisse', 'Mururu', 'Nkanka',
        'Nyakabuye', 'Boneza', 'Mushubati_Paroisse'
    ) THEN 'Rainfall station'
    ELSE station_category
END;

ALTER TABLE stations
MODIFY COLUMN station_category VARCHAR(60) NOT NULL;

INSERT IGNORE INTO instruments (
    instrument_name,
    parameters_taken,
    category,
    description,
    is_active,
    recorded_by_username
)
VALUES
    ('Weather Radome', 'Radar antenna protection', 'Radar', 'Protective enclosure for the weather radar antenna.', TRUE, 'classification_import'),
    ('Robotic Room', 'Upper-air sounding operations', 'Upper air', 'Automated upper-air sounding support equipment.', TRUE, 'classification_import'),
    ('UHF Antenna', 'Upper-air telemetry', 'Upper air', 'Receives UHF telemetry from upper-air instruments.', TRUE, 'classification_import'),
    ('Launcher Vessel', 'Radiosonde launch support', 'Upper air', 'Launch vessel used for upper-air observations.', TRUE, 'classification_import'),
    ('Data Logger', 'Sensor data acquisition and storage', 'Data acquisition', 'Collects and stores observations from connected sensors.', TRUE, 'classification_import'),
    ('Battery', 'Station power supply', 'Power', 'Provides backup or primary electrical power.', TRUE, 'classification_import'),
    ('Anemometer', 'Wind speed and direction', 'Wind', 'Measures wind conditions at an automatic weather station.', TRUE, 'classification_import'),
    ('Rain Gauge', 'Precipitation', 'Precipitation', 'Automatic precipitation measuring instrument.', TRUE, 'classification_import'),
    ('Thermography', 'Continuous temperature record', 'Temperature', 'Records temperature variation over time.', TRUE, 'classification_import'),
    ('Hygrography', 'Continuous humidity record', 'Humidity', 'Records relative humidity variation over time.', TRUE, 'classification_import'),
    ('Solar Radiation', 'Solar radiation', 'Radiation', 'Measures incoming solar radiation.', TRUE, 'classification_import'),
    ('Thermometer', 'Air temperature', 'Temperature', 'Measures air temperature at a climatic station.', TRUE, 'classification_import'),
    ('Stevenson Screen', 'Instrument exposure', 'Enclosure', 'Shields meteorological instruments from direct radiation and precipitation.', TRUE, 'classification_import'),
    ('Raingauge', 'Precipitation', 'Precipitation', 'Manual rainfall measuring instrument.', TRUE, 'classification_import'),
    ('Measuring Cylinder', 'Rainfall depth', 'Precipitation', 'Graduated cylinder used to measure collected rainfall.', TRUE, 'classification_import');

INSERT IGNORE INTO instrument_station_categories (instrument_id, station_category)
SELECT instrument_id, 'Weather radar' FROM instruments WHERE instrument_name = 'Weather Radome'
UNION ALL
SELECT instrument_id, 'Upper air station' FROM instruments WHERE instrument_name IN ('Robotic Room', 'UHF Antenna', 'Launcher Vessel')
UNION ALL
SELECT instrument_id, 'Automatic Weather stations' FROM instruments WHERE instrument_name IN ('Data Logger', 'Battery', 'Anemometer')
UNION ALL
SELECT instrument_id, 'Automatic Raingauge' FROM instruments WHERE instrument_name IN ('Data Logger', 'Battery', 'Rain Gauge')
UNION ALL
SELECT instrument_id, 'Principal stations' FROM instruments WHERE instrument_name IN ('Thermography', 'Hygrography', 'Solar Radiation')
UNION ALL
SELECT instrument_id, 'Climatic stations' FROM instruments WHERE instrument_name IN ('Thermometer', 'Stevenson Screen')
UNION ALL
SELECT instrument_id, 'Rainfall station' FROM instruments WHERE instrument_name IN ('Raingauge', 'Measuring Cylinder');

COMMIT;
