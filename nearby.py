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
        params = urlencode({"q": location, "countrycodes": "kr", "format": "jsonv2", "limit": 1})
        req = Request(
            "https://nominatim.openstreetmap.org/search?" + params,
            headers={"User-Agent": "ParkingFinderLocal/1.0 (local test app)", "Accept-Language": "ko"},
        )
        try:
            _last_request = time.monotonic()
            with urlopen(req, timeout=10) as response:
                data = json.load(response)
            if not data:
                raise ParkingProviderError("검색 위치를 찾지 못했습니다. 동네나 주소를 더 자세히 입력해 주세요.")
            lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
            if not (math.isfinite(lat) and math.isfinite(lon) and 33 <= lat <= 39 and 124 <= lon <= 132):
                raise ValueError("Invalid coordinates")
            result = (lat, lon)
            _geocode_cache[location] = result
            return result
        except ParkingProviderError:
            raise
        except (URLError, OSError, ValueError, KeyError, IndexError, TypeError):
            raise ParkingProviderError("검색 위치를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.") from None


def nearby_parking(location, place_type, limit=None):
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
        if distance <= RADIUS_KM:
            place["distance_km"] = round(distance, 2)
            matches.append(place)
    matches.sort(key=lambda place: place["distance_km"])
    return (matches[:limit] if limit else matches), (latitude, longitude)
