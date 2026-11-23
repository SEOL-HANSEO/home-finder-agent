"""Extract a location from common Korean parking requests without an AI service."""
import re


def parse_request(message):
    text = " ".join(message.split())
    if not text or len(text) > 100:
        return None
    kind = "all"
    if "노외" in text and not re.search(r"노외\s*(?:말고|제외|빼고)", text):
        kind = "off_street"
    elif "노상" in text and not re.search(r"노상\s*(?:말고|제외|빼고)", text):
        kind = "on_street"
    elif re.search(r"노상\s*(?:말고|제외|빼고)", text):
        kind = "off_street"
    elif re.search(r"노외\s*(?:말고|제외|빼고)", text):
        kind = "on_street"
    text = re.sub(r"(?:에서|으로)\s*(?=가장|제일|가까운|주변|근처)", " 근처 ", text, count=1)
    text = re.split(r"\s*(?:근처|주변|인근|부근|가까운|가까이)", text, maxsplit=1)[0]
    text = re.split(r"\s*(?:무료\s*주차장|무료주차장|주차장|주차할\s*곳|주차할곳)", text, maxsplit=1)[0]
    text = re.sub(r"\s*(?:에서|에는|에서는|쪽에|으로|까지)$", "", text).strip(" ,.!?~")
    if not text or text in {"가장", "제일", "무료", "어디", "어디에", "추천", "안녕"}:
        return None
    return text, kind
