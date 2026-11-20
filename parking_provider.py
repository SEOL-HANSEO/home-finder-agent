"""공공데이터포털 전국주차장정보표준데이터 조회."""
import json
from contextvars import ContextVar
import math
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen

API_URL = "https://api.data.go.kr/openapi/tn_pubr_prkplce_info_api"
PAGE_SIZE = 1000
MAX_PAGES = 100
provider_calls = ContextVar("provider_calls", default=0)


class ParkingProviderError(Exception):
    pass


def search_parking(location, place_type, limit=None):
    key = os.environ.get("PARKING_API_KEY", "").strip()
    config_path = Path(__file__).with_name(".env")
    if not key and config_path.exists():
        for line in config_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("PARKING_API_KEY="):
                key = line.partition("=")[2].strip()
                break
    if not key:
        raise ParkingProviderError("공공데이터 API 인증키가 설정되지 않았습니다.")
    query = "".join(location.split()).casefold()
    matches = []
    page = 1
    while page <= MAX_PAGES:
        params = urlencode({
            "serviceKey": unquote(key), "type": "json",
            "pageNo": page, "numOfRows": PAGE_SIZE, "parkingchrgeInfo": "무료",
        })
        try:
            req = Request(f"{API_URL}?{params}", headers={"Accept": "application/json"})
            provider_calls.set(provider_calls.get() + 1)
            with urlopen(req, timeout=20) as response:
                payload = json.load(response)
            root = payload.get("response", payload)
            header = root.get("header", {})
            code = str(header.get("resultCode", "00"))
            if code not in ("00", "0"):
                raise ParkingProviderError("공공데이터 API가 요청을 처리하지 못했습니다. 인증키와 활용신청 상태를 확인해 주세요.")
            body = root["body"]
            items = body.get("items", [])
            if isinstance(items, dict):
                items = items.get("item", [])
            if isinstance(items, dict):
                items = [items]
            if items is None:
                items = []
            if not isinstance(items, list):
                raise ValueError("Unexpected item list")
            total = int(body.get("totalCount", len(items)))
        except ParkingProviderError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, TypeError):
            raise ParkingProviderError("공공데이터를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.") from None

        for item in items:
            if str(item.get("parkingchrgeInfo", "")).strip() != "무료":
                continue
            kind = str(item.get("prkplceType", "")).strip()
            if place_type != "all" and kind != {"off_street": "노외", "on_street": "노상"}[place_type]:
                continue
            address = str(item.get("rdnmadr") or item.get("lnmadr") or "").strip()
            searchable = " ".join(str(item.get(field) or "") for field in ("rdnmadr", "lnmadr", "prkplceNm"))
            if query not in "".join(searchable.split()).casefold():
                continue
            try:
                lat, lon = float(item.get("latitude")), float(item.get("longitude"))
                if not (math.isfinite(lat) and math.isfinite(lon) and 33 <= lat <= 39 and 124 <= lon <= 132):
                    lat = lon = None
            except (TypeError, ValueError):
                lat = lon = None
            matches.append({
                "name": str(item.get("prkplceNm") or "이름 없는 주차장"),
                "area": address or "주소 정보 없음",
                "place_type": "off_street" if kind == "노외" else "on_street" if kind == "노상" else "other",
                "lat": lat, "lon": lon,
                "reference_date": str(item.get("referenceDate") or ""),
                "provider": str(item.get("instt_nm") or ""),
                "spaces": str(item.get("prkcmprt") or ""),
            })
        if limit is not None and len(matches) >= limit:
            return matches[:limit]
        if page * PAGE_SIZE >= total:
            return matches
        if not items:
            raise ParkingProviderError("공공데이터의 일부를 불러오지 못했습니다. 다시 시도해 주세요.")
        page += 1
    raise ParkingProviderError("검색 가능한 데이터 양을 초과했습니다. 잠시 후 다시 시도해 주세요.")
