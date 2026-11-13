"""산출물 · 증거 파일 · summary (설계 §8). 시각은 전부 KST."""
import csv
import io
import json
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
STEAK_HEADER = ["name", "address", "region", "category", "source_name", "source_url", "checked_at_kst", "status",
                "lat", "lng", "coord_source"]
GENERIC_HEADER = ["target_id", "field", "value", "source_url", "match_rule", "checked_at_kst"]
COORD_COLUMNS = ("lat", "lng", "coord_source")
_SIDO_SHORT = {"서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천", "광주광역시": "광주",
               "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종", "경기도": "경기", "강원특별자치도": "강원",
               "강원도": "강원", "충청북도": "충북", "충청남도": "충남", "전북특별자치도": "전북", "전라북도": "전북",
               "전라남도": "전남", "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주", "서울시": "서울",
               "세종시": "세종"}


def now_kst() -> datetime:
    return datetime.now(KST)


def now_iso() -> str:
    return now_kst().isoformat()


def region_of(address: str, fallback: str) -> str:
    """주소 첫 토큰이 시도(긴 이름 · 약칭)면 그 약칭, 아니면 known.sido — 「강남구」 를 region 에 쓰지 않는다."""
    first = (address or "").split(" ")[0] if address else ""
    if first in _SIDO_SHORT:
        return _SIDO_SHORT[first]
    if first in set(_SIDO_SHORT.values()):
        return first
    return fallback or ""


def steak_row(**values) -> Dict[str, str]:
    """좌표 세 열에 값을 넣으려 하면 예외 — 좌표 정책(지오코딩 · 지도 API 좌표 저장 금지)."""
    for col in COORD_COLUMNS:
        if values.get(col):
            raise ValueError(f"COORD_FORBIDDEN: {col} 에 값을 쓸 수 없다")
    return {col: values.get(col, "") or "" for col in STEAK_HEADER}


def build_rows(schema: str, targets, accepted: List[dict], titles: Dict[str, str], checked_date: str) -> tuple:
    if schema == "generic-claims-v1":
        rows = [{"target_id": c["target_id"], "field": c["field"], "value": c["value"], "source_url": c["source_url"],
                 "match_rule": c.get("match_rule", ""), "checked_at_kst": checked_date} for c in accepted]
        return GENERIC_HEADER, rows
    by = {}
    for c in accepted:
        by.setdefault(c["target_id"], {}).setdefault(c["field"], c)
    rows = []
    for t in targets:
        got = by.get(t.id, {})
        addr, url = got.get("address"), got.get("official_url")
        if not addr and not url:
            continue
        src = (addr or url)["source_url"]
        host = urlsplit(src).hostname or ""
        title = (titles.get(src) or "")[:80]
        rows.append(steak_row(
            name=t.known.get("name", ""), address=addr["value"] if addr else "",
            region=region_of(addr["value"] if addr else "", t.known.get("sido", "")),
            category=t.known.get("category") or "steakhouse",
            source_name=f"{title} ({host})" if title else host, source_url=src, checked_at_kst=checked_date,
            status="closed" if "status" in got else "unknown"))
    return STEAK_HEADER, rows


RAW_MAX_BYTES = 10 * 1024 * 1024


def _open_w(path: Path, newline=None):
    """심볼릭 링크를 따라가지 않고 쓴다 — 미리 놓인 링크로 out 밖의 파일을 덮지 못하게(O_NOFOLLOW → ELOOP)."""
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o644)
    return io.open(fd, "w", encoding="utf-8", newline=newline)


def _write_text(path: Path, text: str) -> None:
    with _open_w(path) as fh:
        fh.write(text)


def _mkdir_nofollow(path: Path) -> None:
    path.mkdir(exist_ok=True)
    if path.is_symlink():
        raise OSError(f"OUTPUT_SYMLINK: {path} 가 심볼릭 링크다")


def _jsonl(path: Path, rows: List[dict]) -> None:
    with _open_w(path) as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def raw_within_limit(raw: List[dict], limit: int = RAW_MAX_BYTES) -> List[dict]:
    """항목 단위로 앞에서부터 담는다 — 글자 단위로 자르면 JSON 이 깨진다."""
    out, size = [], 2
    for r in raw:
        n = len(json.dumps(r, ensure_ascii=False).encode("utf-8")) + 2
        if size + n > limit:
            break
        out.append(r)
        size += n
    return out


def write_all(base: Path, *, order_json: dict, header: List[str], rows: List[dict], accepted: List[dict],
              rejected: List[dict], unverifiable: List[dict], claims: List[dict], raw: List[dict],
              llm_calls: List[dict], fetch_log: List[dict], pages: Dict[str, str], robots: Dict[str, str],
              summary: dict) -> None:
    if base.is_symlink():
        raise OSError(f"OUTPUT_SYMLINK: {base} 가 심볼릭 링크다")
    for d in (base / "evidence", base / "evidence" / "pages", base / "evidence" / "robots"):
        _mkdir_nofollow(d)
    _write_text(base / "collect_order.json", json.dumps(order_json, ensure_ascii=False, indent=2))
    with _open_w(base / "accepted.csv", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=header)
        w.writeheader()
        w.writerows(rows)
    _jsonl(base / "accepted.jsonl", accepted)
    _jsonl(base / "rejected.jsonl", rejected)
    _jsonl(base / "unverifiable.jsonl", unverifiable)
    _jsonl(base / "claims.jsonl", claims)
    _write_text(base / "claims_raw.json", json.dumps(raw_within_limit(raw), ensure_ascii=False))
    _jsonl(base / "llm_calls.jsonl", llm_calls)
    _jsonl(base / "evidence" / "fetch_log.jsonl", fetch_log)
    for sha, text in pages.items():
        _write_text(base / "evidence" / "pages" / f"{sha}.txt", text)
    for host, text in robots.items():
        _write_text(base / "evidence" / "robots" / f"{host}.txt", text)
    _write_text(base / "summary.json", json.dumps(summary, ensure_ascii=False, indent=2))


def reason_counts(*groups: List[dict]) -> Dict[str, int]:
    c = Counter()
    for g in groups:
        c.update(r.get("reason_code") for r in g if r.get("reason_code"))
    return dict(c)
