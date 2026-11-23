"""Resolve a location and find nearby parking."""
import json
import math
import threading
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from parking_provider import ParkingProviderError, search_parking

RADIUS_KM = 3
APP_URL = "https://free-parking-finder-j4bh.onrender.com"
_geocode_lock = threading.Lock()
_last_request = 0.0
_geocode_cache = {}


def geocode(location):
    global _last_request
    with _geocode_lock:
        if location in _geocode_cache:
            return _geocode_cache[location]
        delay = 1.1 - (time.monotonic() - _last_request)
        if delay > 0:
            time.sleep(delay)
        result = None
        failed = False
        try:
            params = urlencode({"q": location, "countrycodes": "kr", "format": "jsonv2", "limit": 1})
            req = Request(
                "https://nominatim.openstreetmap.org/search?" + params,
                headers={
                    "User-Agent": f"FreeParkingFinder/1.0 ({APP_URL})",
                    "Referer": APP_URL,
                    "Accept-Language": "ko",
                },
            )
            _last_request = time.monotonic()
            with urlopen(req, timeout=10) as response:
                data = json.load(response)
            if data:
                result = (float(data[0]["lat"]), float(data[0]["lon"]))
        except (URLError, OSError, ValueError, KeyError, IndexError, TypeError):
            failed = True
        if not _valid_coordinates(result):
            try:
                # Photon is a secondary geocoder for when the primary service is unavailable.
                params = urlencode({
                    "q": location, "lang": "ko", "limit": 1,
                    "lat": 36.5, "lon": 127.5,
                    "bbox": "124,33,132,39",
                })
                req = Request(
                    "https://photon.komoot.io/api/?" + params,
                    headers={"User-Agent": f"FreeParkingFinder/1.0 ({APP_URL})", "Referer": APP_URL},
                )
                with urlopen(req, timeout=10) as response:
                    data = json.load(response)
                features = data.get("features", [])
                if features:
                    lon, lat = features[0]["geometry"]["coordinates"][:2]
                    result = (float(lat), float(lon))
            except (URLError, OSError, ValueError, KeyError, IndexError, TypeError):
                failed = True
        if not _valid_coordinates(result):
            try:
                # Open-Meteo is a third, independent fallback for geocoder outages.
                params = urlencode({"name": location, "countryCode": "KR", "count": 10, "language": "ko"})
                req = Request(
                    "https://geocoding-api.open-meteo.com/v1/search?" + params,
                    headers={"User-Agent": f"FreeParkingFinder/1.0 ({APP_URL})", "Accept": "application/json"},
                )
                with urlopen(req, timeout=10) as response:
                    data = json.load(response)
                for place in data.get("results", []):
                    candidate = (float(place["latitude"]), float(place["longitude"]))
                    if _valid_coordinates(candidate):
                        result = candidate
                        break
            except (URLError, OSError, ValueError, KeyError, IndexError, TypeError):
                failed = True
        if not _valid_coordinates(result):
            if failed:
                raise ParkingProviderError("검색 위치를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.")
            raise ParkingProviderError("검색 위치를 찾지 못했습니다. 동네나 주소를 더 자세히 입력해 주세요.")
        _geocode_cache[location] = result
        return result


def _valid_coordinates(result):
    if not result:
        return False
    lat, lon = result
    return math.isfinite(lat) and math.isfinite(lon) and 33 <= lat <= 39 and 124 <= lon <= 132


def nearby_parking(location, place_type, limit=None, radius_km=RADIUS_KM):
    latitude, longitude = geocode(location)
    matches = []
    for place in search_parking("", place_type):
        if place["lat"] is None or place["lon"] is None:
            continue
        lat1, lat2 = math.radians(latitude), math.radians(place["lat"])
        dlat = lat2 - lat1
        dlon = math.radians(place["lon"] - longitude)
        a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
        distance = 6371 * 2 * math.asin(math.sqrt(min(1, a)))
        if distance <= radius_km:
            place["distance_km"] = round(distance, 2)
            matches.append(place)
    matches.sort(key=lambda place: place["distance_km"])
    return (matches[:limit] if limit else matches), (latitude, longitude)
