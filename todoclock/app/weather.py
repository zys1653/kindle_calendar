"""QWeather weather/v1. Provider-specific shapes stop at this boundary."""
from datetime import datetime, timezone, timedelta
import re
import time
from .network import HTTP, ServiceError

CHINA = timezone(timedelta(hours=8))


def api_origin(host):
    host = host.strip()
    if host.startswith("https://"):
        host = host[8:]
    host = host.rstrip("/")
    if not re.fullmatch(r"[A-Za-z0-9-]+\.qweatherapi\.com", host):
        raise ServiceError("API Host 应为控制台提供的 xxx.qweatherapi.com 主机名", kind='CONFIG_ERROR')
    return "https://" + host.lower()


def percent(value):
    if value is None:
        return None
    try:
        value = float(value)
        return round(value * 100) if 0 <= value <= 1 else None
    except (TypeError, ValueError):
        return None


def quantity(item, key):
    data = item.get(key) or {}
    return data.get("value")


def normalize(item):
    precipitation = item.get("precipitation") or {}
    return {
        "text": (item.get("condition") or {}).get("text", "—"),
        "code": (item.get("condition") or {}).get("code", ""),
        "temp": quantity(item, "temperature"),
        "temp_unit": (item.get("temperature") or {}).get("unit", "°C"),
        "feels": quantity(item, "feelsLike"),
        "humidity": percent(item.get("humidity")),
        "wind": quantity(item.get("wind") or {}, "speed"),
        "wind_unit": ((item.get("wind") or {}).get("speed") or {}).get("unit", "m/s"),
        "pop": percent(precipitation.get("probability")),
        "rain": precipitation.get("type") in ("rain", "mixed", "ice"),
        "time": item.get("forecastTime", item.get("time", "")),
    }


def local_date(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(CHINA).date().isoformat()
    except (ValueError, TypeError):
        return ""


class QWeather:
    def __init__(self, credentials, store, http=None):
        self.credentials, self.store = credentials, store
        self.http = http or HTTP()

    @staticmethod
    def cache_key(place):
        return "{:.2f},{:.2f}".format(place["latitude"], place["longitude"])

    def cached(self, place):
        return self.store.read("weather.json", {}).get(self.cache_key(place), {})

    def sync(self, place):
        origin = api_origin(self.credentials.get("api_host", ""))
        key = self.credentials.get("api_key", "").strip()
        if not key:
            raise ServiceError("请填写和风 API Key", kind='CONFIG_ERROR')
        cache = self.cached(place)
        errors = []
        for part in ("current", "daily", "hourly"):
            params = {"lang": "zh", "localTime": "true"}
            if part == "daily":
                params["days"] = 3
            if part == "hourly":
                params["hours"] = 24
            url = origin + "/weather/v1/{}/{:.2f}/{:.2f}".format(
                part, place["latitude"], place["longitude"])
            try:
                data = self.http.request("GET", url, params=params, headers={"X-QW-Api-Key": key})
                if not isinstance(data, dict) or (part == "daily" and not isinstance(data.get("days"), list)) or (part == "hourly" and not isinstance(data.get("hours"), list)) or (part == "current" and "temperature" not in data):
                    raise ServiceError("天气数据结构不完整", kind='SCHEMA_ERROR')
                cache[part] = {"data": data, "fetched": time.time()}
                cache.pop(part + "_error", None)
            except ServiceError as exc:
                exc.stage = part
                cache[part + "_error"] = exc.diagnostic()
                errors.append(exc)
            self.store.update("weather.json", {}, lambda all_data: all_data.update({self.cache_key(place): cache}))
            # Do not multiply requests after an authentication / quota / throttle error.
            if errors and errors[-1].status in (401, 402, 403, 429):
                break
        if errors:
            raise errors[0]
        return cache


def view(cache, today):
    current = normalize(cache.get("current", {}).get("data", {}))
    daily = cache.get("daily", {}).get("data", {}).get("days", [])
    # Daytime identifies the intended calendar day even if its full interval starts the prior evening.
    day = next((d for d in daily if local_date(d.get('daytime', {}).get('forecastStartTime', '')) == today), {})
    if not day:
        day = next((d for d in daily if not d.get('daytime', {}).get('forecastStartTime') and local_date(d.get('forecastStartTime', '')) == today), {})
    hours = [normalize(h) for h in cache.get("hourly", {}).get("data", {}).get("hours", [])]
    rain = any(normalize(day.get(p, {}))["rain"] for p in ("daytime", "nighttime"))
    rain = rain or any((h["rain"] or (h["pop"] or 0) >= 30) for h in hours if local_date(h["time"]) == today)
    attributes = set()
    for part in ("current", "daily", "hourly"):
        attributes.update(cache.get(part, {}).get("data", {}).get("metadata", {}).get("attributions", []))
    return {"current": current, "max": quantity(day, "temperatureMax"),
            "min": quantity(day, "temperatureMin"), "hours": hours, "rain": rain,
            "attributions": sorted(attributes), "fetched": cache.get("current", {}).get("fetched", 0),
            "server_time": cache.get('current', {}).get('data', {}).get('updateTime', ''),
            "errors": [v for k, v in cache.items() if k.endswith("_error")]}
