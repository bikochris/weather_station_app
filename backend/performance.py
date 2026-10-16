"""Monthly station performance validation and score summaries."""
from datetime import date
from pydantic import BaseModel, Field, field_validator, model_validator

PERFORMANCE_REPORT_SCHEMA = """CREATE TABLE IF NOT EXISTS station_performance_reports (
    report_id INT AUTO_INCREMENT PRIMARY KEY,
    original_filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    file_size INT NOT NULL,
    file_data LONGBLOB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""

PERFORMANCE_SCHEMA = """CREATE TABLE IF NOT EXISTS station_performance (
    performance_id INT AUTO_INCREMENT PRIMARY KEY,
    station_id INT NOT NULL,
    report_id INT NULL,
    report_month DATE NOT NULL,
    score DECIMAL(5,2) NOT NULL,
    notes TEXT NULL,
    original_filename VARCHAR(255) NOT NULL,
    content_type VARCHAR(100) NOT NULL,
    file_size INT NOT NULL,
    file_data LONGBLOB NOT NULL,
    created_by_user_id INT NULL,
    created_by_username VARCHAR(50) NOT NULL,
    updated_by_username VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_performance_station_month (station_id, report_month),
    CONSTRAINT fk_performance_shared_report FOREIGN KEY (report_id) REFERENCES station_performance_reports(report_id),
    FOREIGN KEY (station_id) REFERENCES stations(station_id) ON DELETE CASCADE,
    FOREIGN KEY (created_by_user_id) REFERENCES users(user_id) ON DELETE SET NULL
)"""


class PerformanceInput(BaseModel):
    station_id: int = Field(gt=0)
    report_month: date
    score: float = Field(ge=0, le=100, allow_inf_nan=False)
    notes: str = Field(default="", max_length=5000)

    @field_validator("report_month")
    @classmethod
    def first_day(cls, value):
        return value.replace(day=1)


class PerformanceStationScore(BaseModel):
    station_id: int = Field(gt=0)
    score: float = Field(ge=0, le=100, allow_inf_nan=False)


class PerformanceBatch(BaseModel):
    stations: list[PerformanceStationScore] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_stations(self):
        ids = [item.station_id for item in self.stations]
        if len(ids) != len(set(ids)):
            raise ValueError("Select each station only once")
        return self


class PerformanceBulkMonth(BaseModel):
    performance_ids: list[int] = Field(min_length=1, max_length=5000)
    report_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")

    @field_validator("performance_ids")
    @classmethod
    def valid_ids(cls, values):
        if any(value <= 0 for value in values) or len(values) != len(set(values)):
            raise ValueError("Select different valid assessment IDs")
        return values


def performance_summary(items):
    groups = {"stations": {}, "months": {}, "quarters": {}, "years": {}}
    station_periods = {"months": {}, "quarters": {}, "years": {}}
    for item in items:
        month = str(item["report_month"])[:7]
        year, number = map(int, month.split("-"))
        start = year if number >= 7 else year - 1
        fiscal = f"{start}/{start + 1}"
        quarter = (number - 7) % 12 // 3 + 1
        keys = {"stations": str(item["station_id"]), "months": month,
                "quarters": f"{fiscal} Q{quarter}", "years": fiscal}
        for group, key in keys.items():
            groups[group].setdefault(key, []).append(float(item["score"]))
        for group in station_periods:
            station_periods[group].setdefault(keys[group], {}).setdefault(str(item["station_id"]), []).append(float(item["score"]))
    averages = {group: {key: round(sum(values) / len(values), 2) for key, values in entries.items()}
                for group, entries in groups.items()}
    names = {str(item["station_id"]): f'{item["station_code"]} - {item["station_name"]}' for item in items}
    reports = {}
    for item in items:
        if not item.get("original_filename"):
            continue
        key = f'shared-{item["report_id"]}' if item.get("report_id") else f'row-{item["performance_id"]}'
        report = reports.setdefault(key, {"performance_id": item["performance_id"],
            "filename": item["original_filename"], "months": set(), "stations": {}})
        report["months"].add(str(item["report_month"])[:7])
        report["stations"][str(item["station_id"])] = names[str(item["station_id"])]
    return {"total": len(items), "station_count": len(names),
            "average": round(sum(float(item["score"]) for item in items) / len(items), 2) if items else None,
            "months_count": len(groups["months"]), "averages": averages, "station_labels": names,
            "reports": [{**report, "months": sorted(report["months"])} for report in reports.values()],
            "station_period_averages": {group: {period: {station: round(sum(scores) / len(scores), 2)
                for station, scores in stations.items()} for period, stations in periods.items()}
                for group, periods in station_periods.items()}}
