"""Create small, explainable security findings from saved audit events."""
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import re


def _time(value):
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def analyze(logs):
    """Return findings for repeated denials/failures and unusually expensive searches."""
    events = []
    for log in logs:
        created = _time(log.get("created_at"))
        if created:
            events.append((created, log))
    findings = []
    by_actor = defaultdict(list)
    for created, log in events:
        if log.get("outcome") in ("failure", "denied"):
            by_actor[log.get("actor_alias", "guest")].append(created)
        if log.get("action") == "search" and log.get("outcome") == "failure" and int(log.get("provider_calls") or 0) >= 10:
            findings.append(_finding("expensive_search_failure", log.get("actor_alias", "guest"), created,
                                     "검색 실패 한 건에서 외부 데이터 호출이 10회 이상 발생했습니다."))
    for actor, times in by_actor.items():
        times.sort()
        for index, start in enumerate(times):
            window = [stamp for stamp in times[index:] if (stamp - start).total_seconds() <= 900]
            if len(window) >= 5:
                findings.append(_finding("repeated_failures", actor, window[-1],
                                         f"계정 별칭 {actor}에서 15분 안에 실패 또는 거부가 {len(window)}회 기록됐습니다."))
                break
    unique = {item["fingerprint"]: item for item in findings}
    return list(unique.values())


def _finding(rule, actor, created, summary):
    bucket = int(created.timestamp()) // 900
    raw = f"{rule}:{actor}:{bucket}"
    return {
        "fingerprint": hashlib.sha256(raw.encode()).hexdigest(),
        "rule": rule,
        "severity": "high" if rule == "expensive_search_failure" else "medium",
        "actor_alias": actor,
        "summary": summary,
        "observed_at": created.isoformat(),
    }


def chat_safety_finding(actor, message, created):
    """Build a privacy-trimmed report for text blocked by the model safety filter."""
    excerpt = re.sub(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", "[이메일 가림]", message, flags=re.I)
    excerpt = re.sub(r"(?<!\d)(?:\+?82[- .]?)?0?1\d[- .]?\d{3,4}[- .]?\d{4}(?!\d)", "[전화번호 가림]", excerpt)
    excerpt = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+=*", r"\1[인증정보 가림]", excerpt)
    excerpt = re.sub(r"\s+", " ", excerpt).strip()[:160]
    bucket = int(created.timestamp()) // 900
    digest = hashlib.sha256(message.encode("utf-8")).hexdigest()[:16]
    fingerprint = hashlib.sha256(f"chat_safety_block:{actor}:{digest}:{bucket}".encode()).hexdigest()
    return {
        "fingerprint": fingerprint,
        "rule": "chat_safety_block",
        "severity": "medium",
        "actor_alias": actor,
        "summary": "AI 안전 필터가 챗봇 입력을 차단했습니다.",
        "content_excerpt": excerpt,
        "observed_at": created.isoformat(),
    }
