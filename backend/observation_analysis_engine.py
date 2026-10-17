"""Read-only, bounded analytics over canonical numeric observations.

Public signatures::

    comparisons(conn, pairs, rules, start, end, period='hour',
                minutes=60, tolerance=None, *, max_rows=100000,
                max_source_rows=5000000, calm_threshold=0.5)
    monitoring(conn, stations, start, end, period='day', minutes=60,
               *, max_rows=100000, max_source_rows=5000000)

Times are naive ISO local station times; ranges are [start, end). Difference
is automatic minus manual. Summary errors use available matched hourly pairs;
period averages retain each side's available numeric readings independently.
No automatic QC rejection or default tolerance classification is applied.
Daily comparisons remain daily for every
requested period. Daily labels identify the start date of [08:00, next 08:00).
AWS daily values use every available numeric reading, including off-grid
timestamps. Coverage metadata is informational and never suppresses values. Manual daily
values are already aggregates, associated with their stamped calendar date.

Methods: hourly/exact/mean (exact hour pairs), max/daily_max, min/daily_min,
sum/daily_sum. Manual elements 2, 3 and 5 always select max, min and sum.
The primary key supplies deduplication; ingestion owns latest-numeric-wins.
No registry, ingestion, schema changes, commits, or permission checks occur.
"""

from collections import defaultdict
from datetime import datetime, timedelta
import math
from numbers import Real


_FORMAT = "%Y-%m-%d %H:%M"
_HOUR = timedelta(hours=1)
_DAY = timedelta(days=1)
_DAILY = {"2": "max", "3": "min", "5": "sum"}
_RAIN = {"5", "1107"}
_SPEED = {"196", "1092"}
_DIRECTION = {"112", "1042"}


def _stamp(value):
    return value.strftime(_FORMAT)


def _settings(start, end, period, minutes, max_rows):
    start, end = datetime.fromisoformat(start), datetime.fromisoformat(end)
    if start.tzinfo or end.tzinfo or start >= end:
        raise ValueError("Use naive ISO times with start before end")
    if period not in {"hour", "day", "week", "month", "custom"}:
        raise ValueError("Unknown period")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise ValueError("minutes must be a positive integer")
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows <= 0:
        raise ValueError("max_rows must be a positive integer")
    return start, end


def _bucket(time, period, minutes, start):
    if period == "custom":
        return start + timedelta(minutes=int((time - start).total_seconds() // (minutes * 60)) * minutes)
    day = time.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "hour":
        return time.replace(minute=0, second=0, microsecond=0)
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    return day


def _mean(values):
    return math.fsum(values) / len(values) if values else None


class _Readings(dict):
    def __init__(self):
        super().__init__()
        self.met_days = defaultdict(list)


class _Reader:
    def __init__(self, conn, start, end, max_rows):
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows <= 0:
            raise ValueError("max_source_rows must be a positive integer")
        self.conn, self.start, self.end = conn, start, end
        self.limit, self.loaded = max_rows, 0
        self.cache, self.warnings = {}, set()

    def warn(self, message):
        self.warnings.add(message)

    def read(self, station, element, daily=False, meteorological=False):
        key = str(station), str(element), daily, meteorological
        if key in self.cache:
            return self.cache[key]
        # Equality on the PK prefix followed by a stamp range uses its index.
        query_start = self.start.replace(hour=0, minute=0, second=0, microsecond=0) if daily else self.start
        query_end = self.end.replace(hour=0, minute=0, second=0, microsecond=0) + _DAY if daily else self.end
        if meteorological:
            if self.start.hour == 0 and self.start.minute == 0: query_start += timedelta(hours=8)
            if self.end.hour == 0 and self.end.minute == 0: query_end += timedelta(hours=8)
        cursor = self.conn.execute(
            "SELECT stamp,value FROM observations "
            "WHERE station=? AND element=? AND stamp>=? AND stamp<? "
            "ORDER BY stamp LIMIT ?",
            (*key[:2], _stamp(query_start), _stamp(query_end if not (query_end.second or query_end.microsecond)
                else query_end + timedelta(minutes=1)),
             self.limit - self.loaded + 1),
        )
        result = _Readings()
        for stamp, value in cursor:
            self.loaded += 1
            if self.loaded > self.limit:
                raise ValueError("Too many observations; narrow the range")
            time = datetime.strptime(stamp, _FORMAT)
            if not daily and not query_start <= time < query_end:
                continue
            if daily and not self.start <= time.replace(hour=8, minute=0) < self.end:
                continue
            if not isinstance(value, Real) or not math.isfinite(value):
                self.warn("Missing or non-finite numeric readings excluded")
                continue
            result[time] = float(value)
            result.met_days[(time - timedelta(hours=8)).date()].append(float(value))
        self.cache[key] = result
        return result


def _ticks(start, end, step, limit):
    count = max(0, math.ceil((end - start) / step))
    if count > limit:
        raise ValueError("Too many result rows; narrow the range")
    return (start + index * step for index in range(count))


def _daily_manual(values, reader):
    result = {}
    for time, value in values.items():
        day = time.replace(hour=0, minute=0, second=0, microsecond=0)
        if day in result:
            reader.warn("Multiple manual daily readings: latest timestamp used")
        result[day] = value
    return result


def _daily_auto(values, day, start, end, method):
    opening = day + timedelta(hours=8)
    slots = [opening + timedelta(minutes=10 * index) for index in range(144)]
    present = values.met_days.get(day.date(), []) if isinstance(values, _Readings) else [value for time, value in values.items() if opening <= time < opening + _DAY]
    range_start = start + timedelta(hours=8) if start.hour == 0 and start.minute == 0 else start
    range_end = end + timedelta(hours=8) if end.hour == 0 and end.minute == 0 else end
    complete = range_start <= opening and opening + _DAY <= range_end and all(slot in values for slot in slots)
    aggregate = {"max": max, "min": min, "sum": math.fsum}[method]
    return aggregate(present) if present else None, len(present), complete


def _errors(native):
    differences = [row["difference"] for row in native if row["difference"] is not None]
    return {"paired": len(differences), "missing": len(native) - len(differences),
            "inconsistent": sum(row["inconsistent"] is True for row in native),
            "bias": _mean(differences), "mae": _mean([abs(v) for v in differences]),
            "rmse": math.hypot(*differences) / math.sqrt(len(differences)) if differences else None}


def _correlation_moments(rows):
    matched = [(row["manual"], row["automatic"]) for row in rows
               if row["manual"] is not None and row["automatic"] is not None]
    if not matched:
        return (0, 0, 0, 0, 0, 0)
    x = _mean([pair[0] for pair in matched])
    y = _mean([pair[1] for pair in matched])
    return (len(matched), x, y,
            math.fsum((a - x) ** 2 for a, _ in matched),
            math.fsum((b - y) ** 2 for _, b in matched),
            math.fsum((a - x) * (b - y) for a, b in matched))


def _regression(parts):
    # OLS with an intercept: automatic = slope * manual + intercept.
    n = 0
    x = y = xx = yy = xy = 0.0
    for count, mx, my, vx, vy, cov in parts:
        if not count:
            continue
        total = n + count
        dx, dy = mx - x, my - y
        weight = n * count / total
        xx += vx + dx * dx * weight
        yy += vy + dy * dy * weight
        xy += cov + dx * dy * weight
        x += dx * count / total
        y += dy * count / total
        n = total
    if n < 2 or xx <= 0 or yy <= 0:
        return dict(r_squared=None, regression_slope=None, regression_intercept=None)
    slope = xy / xx
    residual_sum_squares = max(0.0, yy - slope * xy)
    return dict(r_squared=max(0.0, min(1.0, 1 - residual_sum_squares / yy)),
                regression_slope=slope, regression_intercept=y - slope * x)


def _row(pair, definition, time, manual, automatic, mc, ac, expected, complete, tolerance):
    difference = automatic - manual if manual is not None and automatic is not None else None
    return {"site": pair.get("label", pair["id"]),
            "manual_station": str(pair["manual_code"]), "automatic_station": str(pair["automatic_code"]),
            "parameter": definition.get("label", definition["id"]), "time": _stamp(time),
            "manual": manual, "automatic": automatic, "difference": difference,
            "inconsistent": abs(difference) > tolerance if difference is not None and tolerance is not None else None,
            "manual_count": mc, "automatic_count": ac, "expected_automatic": expected,
            "complete": complete, "partial_day": expected == 144 and ac < 144,
            "site_id": pair["id"], "parameter_id": definition["id"],
            "manual_element": str(definition["manual_element"]),
            "automatic_element": str(definition["automatic_element"])}


def _roses(reader, pairs, calm_threshold):
    roses = {side: {"bins": [0] * 16, "calm": 0, "total": 0}
             for side in ("manual", "automatic")}
    for pair in pairs:
        reader.cache.clear()
        streams = [reader.read(pair["manual_code"], "112"), reader.read(pair["manual_code"], "196"),
                   reader.read(pair["automatic_code"], "1042"), reader.read(pair["automatic_code"], "1092")]
        common = set(streams[0]).intersection(*streams[1:])
        for time in common:
            if time.minute != 0:
                continue
            for side, offset in (("manual", 0), ("automatic", 2)):
                rose = roses[side]
                rose["total"] += 1
                if streams[offset + 1][time] <= calm_threshold:
                    rose["calm"] += 1
                else:
                    sector = int(((streams[offset][time] % 360) + 11.25) // 22.5) % 16
                    rose["bins"][sector] += 1
    reader.cache.clear()
    return roses


def _merge_errors(parts):
    paired = sum(p["paired"] for p in parts)
    return {"paired": paired, "missing": sum(p["missing"] for p in parts),
            "inconsistent": sum(p["inconsistent"] for p in parts),
            "bias": math.fsum(p["bias"] * p["paired"] for p in parts if p["paired"]) / paired if paired else None,
            "mae": math.fsum(p["mae"] * p["paired"] for p in parts if p["paired"]) / paired if paired else None,
            "rmse": math.hypot(*(p["rmse"] * math.sqrt(p["paired"]) for p in parts if p["paired"]))
                    / math.sqrt(paired) if paired else None}


def _chart_series(native, methods, period, minutes, start):
    groups = defaultdict(list)
    for row in native:
        time = _bucket(datetime.strptime(row["time"], _FORMAT), period, minutes, start)
        groups[(row["site_id"], row["parameter_id"], time)].append(row)
    series = []
    for (_, parameter_id, time), group in groups.items():
        method = methods[parameter_id]
        aggregate = {"sum": math.fsum, "max": max, "min": min, "mean": _mean}[method]
        selected = group
        item = {key: group[0][key] for key in ("site", "site_id", "parameter", "parameter_id",
                                              "manual_station", "automatic_station", "manual_element", "automatic_element")}
        if method == "mean" and period == "hour":
            item.update(time=_stamp(time), aggregation="exact_hour",
                manual=group[0]["manual"], automatic=group[0]["automatic"],
                complete=group[0]["complete"], automatic_count=group[0]["automatic_count"],
                expected_automatic=1)
            series.append(item)
            continue
        item.update(time=_stamp(time), aggregation=method, complete=all(r["complete"] for r in group),
                    automatic_count=sum(r["automatic_count"] for r in group),
                    expected_automatic=sum(r["expected_automatic"] for r in group))
        for side in ("manual", "automatic", "observed_automatic"):
            values = [r[side] for r in selected if r.get(side) is not None]
            item[side] = aggregate(values) if values else None
        series.append(item)
    return series


def comparisons(conn, pairs, rules, start, end, period="hour", minutes=60,
                tolerance=None, *, max_rows=100000, max_source_rows=5000000, calm_threshold=0.5):
    """Return rows, native-pair summary, bucket series, paired roses and warnings.

    Hourly rows match exact :00 readings. Larger buckets average all available
    readings per side, without rejecting unmatched hours. Counts remain visible.
    Series retains site and parameter identity. Global error statistics are
    None for multiple parameters; per_parameter contains native-pair metrics.
    """
    start, end = _settings(start, end, period, minutes, max_rows)
    if tolerance is not None and (not isinstance(tolerance, Real) or not math.isfinite(tolerance) or tolerance < 0):
        raise ValueError("tolerance must be finite and nonnegative")
    if not isinstance(calm_threshold, Real) or not math.isfinite(calm_threshold) or calm_threshold < 0:
        raise ValueError("calm_threshold must be finite and nonnegative")
    reader = _Reader(conn, start, end, max_source_rows)
    rows, series, summary_parts, native = [], [], [], []
    parameter_parts = defaultdict(list)
    correlation_parts = defaultdict(list)
    parameter_labels, methods = {}, {}
    processed_native = 0
    for pair in pairs:
        reader.cache.clear()
        native = []
        for definition in rules:
            me, ae = str(definition["manual_element"]), str(definition["automatic_element"])
            method = str(definition.get("method", "hourly")).lower()
            method = _DAILY.get(me, method.removeprefix("daily_"))
            if method not in {"hourly", "exact", "mean", "max", "min", "sum"}:
                raise ValueError("Unsupported comparison method: " + method)
            is_daily = method in {"max", "min", "sum"}
            methods[definition["id"]] = method if is_daily else "mean"
            parameter_labels[definition["id"]] = definition.get("label", definition["id"])
            manual = reader.read(pair["manual_code"], me, daily=is_daily)
            auto = reader.read(pair["automatic_code"], ae, meteorological=is_daily)
            for side, code, element, values in [("manual", pair["manual_code"], me, manual),
                                                ("automatic", pair["automatic_code"], ae, auto)]:
                if not values:
                    reader.warn(f"{pair.get('label', pair['id'])}: no numeric {side} element {element} readings for station {code} in this range. Its curve cannot be drawn.")
                elif not is_daily and not any(time.minute == 0 for time in values):
                    reader.warn(f"{pair.get('label', pair['id'])}: no exact :00 {side} element {element} readings for station {code} in this range. Off-hour readings are not substituted.")
            if method in {"max", "min", "sum"}:
                manual = _daily_manual(manual, reader)
                first = start.replace(hour=0, minute=0, second=0, microsecond=0)
                for day in _ticks(first, end - timedelta(hours=8), _DAY,
                                  min(max_rows - len(rows), max_source_rows - processed_native - len(native))):
                    if day + timedelta(hours=8) < start:
                        continue
                    observed, ac, complete = _daily_auto(auto, day, start, end, method)
                    av = observed
                    mv = manual.get(day)
                    row = _row(pair, definition, day, mv, av, int(mv is not None), ac, 144,
                               complete and mv is not None, tolerance)
                    row["partial_day"] = not complete
                    row["observed_automatic"] = observed
                    native.append(row)
                    rows.append(row)
                continue
            first = start.replace(minute=0, second=0, microsecond=0)
            if first < start:
                first += _HOUR
            buckets = defaultdict(list)
            for time in _ticks(first, end, _HOUR, max_source_rows - processed_native - len(native)):
                mv, av = manual.get(time), auto.get(time)
                row = _row(pair, definition, time, mv, av, int(mv is not None), int(av is not None),
                           1, mv is not None and av is not None, tolerance)
                native.append(row)
                buckets[_bucket(time, period, minutes, start)].append(row)
            for time, group in buckets.items():
                paired = [r for r in group if r["difference"] is not None]
                selected = group
                mv = _mean([r["manual"] for r in selected if r["manual"] is not None])
                av = _mean([r["automatic"] for r in selected if r["automatic"] is not None])
                row = _row(pair, definition, time, mv, av,
                           sum(r["manual_count"] for r in group), sum(r["automatic_count"] for r in group),
                           len(group), all(r["complete"] for r in group), tolerance)
                row["inconsistent"] = any(r["inconsistent"] for r in paired) if paired and tolerance is not None else None
                rows.append(row)
                if len(rows) > max_rows:
                    raise ValueError("Too many result rows; narrow the range")
        processed_native += len(native)
        summary_parts.append(_errors(native))
        parameter_groups = defaultdict(list)
        for row in native:
            parameter_groups[row["parameter_id"]].append(row)
        for key, group in parameter_groups.items():
            parameter_parts[key].append(_errors(group))
            correlation_parts[key].append(_correlation_moments(group))
        series.extend(_chart_series(native, methods, period, minutes, start))
        if len(series) > max_rows:
            raise ValueError("Too many series rows; narrow the range")
    native.clear()
    roses = _roses(reader, pairs, calm_threshold)
    per_parameter = [{"parameter_id": key, "parameter": parameter_labels[key],
                      **_merge_errors(parts), **_regression(correlation_parts[key])}
                     for key, parts in parameter_parts.items()]
    summary = _merge_errors(summary_parts)
    summary["r_squared"] = per_parameter[0]["r_squared"] if len(per_parameter) == 1 else None
    if len(parameter_parts) > 1:
        summary.update(bias=None, mae=None, rmse=None)
    return {"rows": sorted(rows, key=lambda r: (r["time"], r["site"], r["parameter"])),
            "summary": summary, "per_parameter": per_parameter, "series": series, "roses": roses,
            "warnings": sorted(reader.warnings)}


def geographic_monitoring(conn, stations, start, end, *, max_source_rows=5000000):
    """Compact map totals, independent of record-table limits and station filters."""
    start, end = _settings(start, end, "day", 60, 100000)
    reader = _Reader(conn, start, end, max_source_rows)
    rainfall, wind = [], []
    for station in stations:
        reader.cache.clear()
        code = str(station["station_code"])
        manual = station["station_category"] in {"Principal Station", "Climatic Station", "Rainfall Station"}
        rain_element, wind_element = ("5", "196") if manual else ("1107", "1092")
        rains = reader.read(code, rain_element, daily=manual, meteorological=not manual)
        rain_values = _daily_manual(rains, reader).values() if manual else rains.values()
        rain_values = list(rain_values)
        speeds = [value for time, value in reader.read(code, wind_element).items() if time.minute == 0]
        identity = dict(station_code=code, station_name=station["station_name"], time=_stamp(start))
        rainfall.append(dict(identity, element=rain_element, value=math.fsum(rain_values) if rain_values else None))
        wind.append(dict(identity, element=wind_element, wind=max(speeds) if speeds else None))
    return dict(daily_rows=rainfall, series=wind, warnings=sorted(reader.warnings))


def monitoring(conn, stations, start, end, period="day", minutes=60, *, max_rows=100000,
               max_source_rows=5000000):
    """Return rain_rows, daily_max, wind_max, series, summary and warnings.

    rain_rows contains actual positive readings, including partial AWS days.
    daily_max and summary max_daily_rainfall include all available daily totals.
    Coverage metadata never excludes measurements. Wind uses exact hours only.
    Series retains station/element identity and sums rain or takes maximum wind;
    absent channels are None, and observed zero remains zero. Manual and AWS
    rainfall series are separate to avoid double counting co-located instruments.
    AWS rain day/week/month buckets use time minus eight hours. Hour and custom
    buckets use actual timestamps; custom buckets are anchored at start. Manual
    rain series uses the latest daily reading, at its actual timestamp, per date.
    """
    start, end = _settings(start, end, period, minutes, max_rows)
    reader = _Reader(conn, start, end, max_source_rows)
    rain_rows, daily_max, wind_max, daily_rows = [], [], [], []
    buckets = defaultdict(lambda: {"rainfall": None, "wind": None})
    daily_count = 0
    for station in stations:
        reader.cache.clear()
        code = str(station["station_code"])
        name = station.get("station_name", code)
        rains = {element: reader.read(code, element, daily=element == "5") for element in ("5", "1107")}
        daily_auto = reader.read(code, "1107", meteorological=True)
        winds = {element: {time: value for time, value in reader.read(code, element).items() if time.minute == 0}
                 for element in ("196", "1092")}
        manual_daily = _daily_manual(rains["5"], reader)
        latest_manual_times = {time.date(): time for time in rains["5"]}
        station_daily = [{"station_code": code, "station_name": name, "element": "5",
                          "time": _stamp(day), "value": value, "automatic_count": 0,
                          "manual_count": 1, "expected_automatic": 0, "complete": True, "partial_day": False}
                         for day, value in manual_daily.items()]
        aws_days = sorted({(time - timedelta(hours=8)).replace(hour=0, minute=0, second=0, microsecond=0)
                           for time in daily_auto})
        daily_count += len(aws_days)
        if daily_count > max_rows:
            raise ValueError("Too many result rows; narrow the range")
        for day in aws_days:
            value, count, complete = _daily_auto(daily_auto, day, start, end, "sum")
            station_daily.append({"station_code": code, "station_name": name, "element": "1107",
                               "time": _stamp(day), "value": value, "automatic_count": count,
                               "expected_automatic": 144, "complete": complete, "partial_day": not complete})
        daily_rows.extend(station_daily)
        if len(daily_rows) > max_rows:
            raise ValueError("Too many daily rows; narrow the range")
        available = sorted((r for r in station_daily if r["value"] is not None),
                           key=lambda r: (r["time"], r["element"]))
        highest = max(available, key=lambda r: r["value"], default=None)
        trusted = [r["value"] for r in available]
        daily_max.append({**(highest or {"station_code": code, "station_name": name, "value": None,
                                       "time": None, "element": None, "automatic_count": 0,
                                       "expected_automatic": 144, "complete": False, "partial_day": True}),
                          "trusted_value": max(trusted) if trusted else None})
        speeds = sorted((time, element, value) for element, stream in winds.items() for time, value in stream.items())
        peak = max(speeds, key=lambda item: item[2], default=None)
        wind_max.append({"station_code": code, "station_name": name,
                         "value": peak[2] if peak else None, "time": _stamp(peak[0]) if peak else None,
                         "element": peak[1] if peak else None})
        for element, stream in rains.items():
            for time, value in stream.items():
                if value != 0:
                    rain_rows.append({"station_code": code, "station_name": name,
                                      "element": element, "time": _stamp(time), "value": value})
                    if len(rain_rows) > max_rows:
                        raise ValueError("Too many rainfall rows; narrow the range")
            chart_stream = ({latest_manual_times[day.date()]: value for day, value in manual_daily.items()}
                            if element == "5" else daily_auto if period in {"day", "week", "month"} else stream)
            for time, value in chart_stream.items():
                chart_time = time - timedelta(hours=8) if element == "1107" and period in {"day", "week", "month"} else time
                group = buckets[(code, name, element, _bucket(chart_time, period, minutes, start))]
                group["rainfall"] = (group["rainfall"] or 0) + value
                if len(buckets) > max_rows:
                    raise ValueError("Too many series rows; narrow the range")
        for element, stream in winds.items():
            for time, value in stream.items():
                group = buckets[(code, name, element, _bucket(time, period, minutes, start))]
                group["wind"] = max(group["wind"], value) if group["wind"] is not None else value
                if len(buckets) > max_rows:
                    raise ValueError("Too many series rows; narrow the range")
    if max(len(rain_rows), len(daily_rows), len(daily_max), len(wind_max)) > max_rows:
        raise ValueError("Too many result rows; narrow the range")
    series = [{"station_code": code, "station_name": name, "element": element, "time": _stamp(time),
               "rainfall": group["rainfall"], "wind": group["wind"],
               "aggregation": "sum" if element in _RAIN else "max"}
              for (code, name, element, time), group in sorted(buckets.items())]
    daily_values = [r["trusted_value"] for r in daily_max if r["trusted_value"] is not None]
    observed_values = [r["value"] for r in daily_max if r["value"] is not None]
    wind_values = [r["value"] for r in wind_max if r["value"] is not None]
    return {"rain_rows": sorted(rain_rows, key=lambda r: (r["time"], r["station_code"], r["element"])),
            "daily_max": daily_max, "daily_rows": daily_rows, "wind_max": wind_max, "series": series,
            "summary": {"rain_records": len(rain_rows),
                        "max_daily_rainfall": max(daily_values) if daily_values else None,
                        "observed_daily_max": max(observed_values) if observed_values else None,
                        "max_wind": max(wind_values) if wind_values else None},
            "warnings": sorted(reader.warnings)}
