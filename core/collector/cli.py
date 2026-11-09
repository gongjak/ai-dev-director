"""python -m core.collector.cli <order.json> --out <dir>  (설계 §2 · §9)

종료값 — 0 정상(accepted 0 건 포함) · 1 지시서 거부 · 2 공급자 실패 · 3 내부 오류
"""
import argparse
import hashlib
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional

from core.collector import outputs
from core.collector.netfetch import Fetcher, precheck, strip_fragment
from core.collector.order import OrderError, load_order
from core.collector.providers import CallFailed, HaikuCollector, LlmBudgetExhausted, ProviderFatal, report_health
from core.collector.verify import conflict_keys, extract_text, road_key, verify_claim


def _rec(target_id: str, field: str, source_url: Optional[str], reason: str, **extra) -> dict:
    return {"target_id": target_id, "field": field, "source_url": source_url, "reason_code": reason, **extra}


def run_collect(order_path, out_dir, *, fetcher: Optional[Fetcher] = None,
                provider: Optional[HaikuCollector] = None) -> tuple:
    """(exit_code, summary). 지시서 거부는 OrderError 로 올린다."""
    started = time.monotonic()
    order = load_order(order_path)
    base = Path(out_dir) / order.order_id
    out_root = Path(out_dir).resolve()
    if base.resolve() == out_root or out_root not in base.resolve().parents:   # out 바로 아래만 쓴다
        raise OrderError(f"ORDER_INVALID: order_id 가 out 폴더 밖을 가리킨다 ({order.order_id})")
    lim = order.limits
    fetcher = fetcher or Fetcher(lim, order.domains, now_iso=outputs.now_iso)
    provider = provider or HaikuCollector(lim.llm_timeout_sec, lim.max_llm_calls, now_iso=outputs.now_iso)
    base.mkdir(parents=True, exist_ok=True)

    rejected: List[dict] = []
    unverifiable: List[dict] = []
    claims: List[dict] = []
    not_attempted: List[str] = []
    pages: Dict[str, str] = {}
    titles: Dict[str, str] = {}
    truncated_pages = 0
    tried: Dict[tuple, bool] = {}            # (target_id, field) → 페이지를 하나라도 읽었는가

    # ① URL 찾기 — 주어진 urls 가 있으면 그대로
    url_targets: Dict[str, list] = {}
    for t in order.targets:
        urls = list(t.urls)
        if not urls:
            try:
                urls = provider.discover_urls(t, lim.max_urls_per_target)
            except (ProviderFatal, LlmBudgetExhausted):
                not_attempted.append(t.id)
                continue
            except CallFailed as exc:
                for f in t.fields:
                    unverifiable.append(_rec(t.id, f, None, "DISCOVERY_FAILED", detail=str(exc)))
                continue
        kept = []
        for u in urls[:lim.max_urls_per_target]:
            reason = precheck(u, order.domains)
            if reason:
                for f in t.fields:
                    rejected.append(_rec(t.id, f, u, reason))
            else:
                kept.append(strip_fragment(u))
        if not kept:
            if not any(r["target_id"] == t.id for r in rejected + unverifiable):
                for f in t.fields:
                    unverifiable.append(_rec(t.id, f, None, "NO_SOURCE_URL"))
            continue
        for u in dict.fromkeys(kept):
            url_targets.setdefault(u, []).append(t)

    # ②~⑤ 받기 → 추출 → 같은 본문에 대조
    for url, targets in url_targets.items():
        if provider.fatal or provider.calls_made >= lim.max_llm_calls:
            not_attempted.extend(t.id for t in targets if t.id not in not_attempted)
            continue
        res = fetcher.fetch(url)
        if not res.ok:
            bucket = rejected if res.outcome == "rejected" else unverifiable
            for t in targets:
                for f in t.fields:
                    bucket.append(_rec(t.id, f, url, res.reason_code, http_status=res.status))
            continue
        page = extract_text(res.body, res.content_type)
        if page is None:
            for t in targets:
                for f in t.fields:
                    unverifiable.append(_rec(t.id, f, res.final_url, "UNSUPPORTED_CONTENT",
                                             content_type=res.content_type))
            continue
        full = page.full()
        sha = hashlib.sha256(full.encode("utf-8")).hexdigest()
        pages[sha] = full
        titles[res.final_url] = page.title
        if len(full) > lim.max_page_chars:
            truncated_pages += 1
        try:
            raw_claims = provider.extract_claims(targets, full[:lim.max_page_chars])
        except (ProviderFatal, LlmBudgetExhausted):
            not_attempted.extend(t.id for t in targets if t.id not in not_attempted)
            continue
        except CallFailed as exc:
            for t in targets:
                for f in t.fields:
                    unverifiable.append(_rec(t.id, f, res.final_url, "EXTRACT_FAILED", detail=str(exc)))
            continue
        by_id = {t.id: t for t in targets}
        for t in targets:
            for f in t.fields:
                tried[(t.id, f)] = True
        limit = len(targets) * 3 * 3
        for c in raw_claims[:limit]:
            tid, f = str(c.get("target_id", "")), str(c.get("field", ""))
            claim = {"target_id": tid, "field": f, "value": c.get("value"), "quote": str(c.get("quote", ""))[:300],
                     "source_url": res.final_url, "page_sha256": sha, "provider": "claude-haiku",
                     "call_no": provider.calls_made, "received_at_kst": outputs.now_iso()}
            claims.append(claim)
            t = by_id.get(tid)
            if t is None:
                rejected.append({**claim, "reason_code": "UNKNOWN_TARGET"})
                continue
            if f not in t.fields:
                rejected.append({**claim, "reason_code": "FIELD_NOT_REQUESTED"})
                continue
            v = verify_claim(f, c.get("value"), res.final_url, t.known.get("name", ""), page, order.domains)
            row = {**claim, "reason_code": v.reason_code, "match_rule": v.match_rule}
            if v.outcome == "accepted":
                row["value"] = v.value
                if v.match_rule == "tokens":
                    row["window"] = v.window
                claims[-1]["verdict"] = "accepted"
                claims[-1]["_row"] = row
            else:
                rejected.append(row)

    # ⑨ 모순 · 중복 정리
    acc_rows = [c.pop("_row") for c in claims if "_row" in c]
    conflicts = conflict_keys(acc_rows)
    accepted: List[dict] = []
    seen = set()
    # 같은 곳을 가리키는 값이 여럿이면 도로명 주소가 있는 값을 먼저 고른다(지번만 있는 값은 뒤로)
    acc_rows.sort(key=lambda r: r["field"] == "address" and road_key(r["value"]) is None)
    for r in acc_rows:
        key = (r["target_id"], r["field"])
        if key in conflicts:
            unverifiable.append({**r, "reason_code": "CONFLICT"})
        elif key not in seen:
            seen.add(key)
            accepted.append(r)
    claimed = {(c["target_id"], c["field"]) for c in claims}
    for key in tried:                         # 읽은 페이지 어디에서도 값을 뽑지 못했다
        if key not in claimed:
            unverifiable.append(_rec(key[0], key[1], None, "NOT_EXTRACTED"))

    header, rows = outputs.build_rows(order.output.schema_name, order.targets, accepted, titles,
                                      outputs.now_kst().date().isoformat())
    failed = provider.calls_made > 0 and provider.successes == 0
    if order.providers.report_health_db and provider.calls_made:
        report_health(provider.successes > 0, provider.stderr_tail)
    hosts: Dict[str, int] = {}
    for e in fetcher.log:
        h = e["url"].split("/")[2] if "://" in e["url"] else e["url"]
        hosts[h] = hosts.get(h, 0) + 1
    summary = {
        "order_id": order.order_id, "status": "PROVIDER_FAILURE" if failed else "OK",
        "targets": len(order.targets), "claims": len(claims), "accepted": len(accepted), "rows": len(rows),
        "rejected": len(rejected), "unverifiable": len(unverifiable),
        "reason_codes": outputs.reason_counts(rejected, unverifiable),
        "fetch_requests": fetcher.requests_made, "requests_per_host": hosts,
        "llm_calls": provider.calls_made, "llm_successes": provider.successes, "provider": "claude-haiku",
        "provider_fatal": provider.fatal, "not_attempted": not_attempted, "truncated_pages": truncated_pages,
        "duration_sec": round(time.monotonic() - started, 2), "finished_at_kst": outputs.now_iso(),
    }
    outputs.write_all(base, order_json=order.model_dump(by_alias=True), header=header, rows=rows, accepted=accepted,
                      rejected=rejected, unverifiable=unverifiable, claims=claims, raw=provider.raw_log,
                      llm_calls=provider.calls_log, fetch_log=fetcher.log, pages=pages, robots=fetcher.robots_texts,
                      summary=summary)
    return (2 if failed else 0), summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m core.collector.cli", description="웹 자료 수집 · 검증")
    ap.add_argument("order", help="collect_order.json 경로")
    ap.add_argument("--out", required=True, help="산출물 폴더(그 아래 <order_id>/)")
    args = ap.parse_args(argv)
    try:
        code, summary = run_collect(args.order, args.out)
    except OrderError as exc:
        print(f"지시서 거부: {exc}", file=sys.stderr)
        return 1
    except Exception:
        traceback.print_exc()
        return 3
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
