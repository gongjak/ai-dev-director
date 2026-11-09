"""스테이크 pending CSV(review.csv · notfound.csv) → collect_order.json (설계 §3).

python scripts/make_collect_order.py <pending 폴더> --order-id steak-pending-20261109 --out order.json [--limit 40]
`entry` 는 한글 · 공백이 있어 id 로 쓸 수 없으므로 `review-001` 처럼 번호를 붙이고 원문은 known.entry 에 둔다.
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def build_order(pending_dir: Path, order_id: str, fields, limit: int, output_path: str) -> dict:
    targets = []
    for name in ("review.csv", "notfound.csv"):
        path = pending_dir / name
        if not path.exists():
            continue
        prefix = path.stem
        with path.open(encoding="utf-8", newline="") as fh:
            for i, row in enumerate(csv.DictReader(fh), 1):
                known = {"name": row["name_part"].strip(), "area": row["area"].strip(), "sido": row["sido"].strip(),
                         "category": "steakhouse", "entry": row["entry"].strip()}
                targets.append({"id": f"{prefix}-{i:03d}", "known": known, "fields": list(fields),
                                "query_hint": f"{known['area']} {known['name']} 스테이크 주소"})
    targets = targets[:limit]
    return {
        "version": 1, "order_id": order_id,
        "purpose": "스테이크 식당의 현재 도로명 주소와 공식 페이지",
        "targets": targets,
        "output": {"schema": "steak-sources-v1", "path": output_path},
        "limits": {"max_targets": max(len(targets), 1)},
        "requested_at_kst": datetime.now(KST).isoformat(timespec="seconds"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pending_dir")
    ap.add_argument("--order-id", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fields", default="address,official_url")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--output-path", default=f"data/sources/web-{datetime.now(KST):%Y%m%d}.csv")
    args = ap.parse_args(argv)
    order = build_order(Path(args.pending_dir), args.order_id, args.fields.split(","), args.limit, args.output_path)
    if not order["targets"]:
        print("대상이 없다", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(order, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(order['targets'])} 대상 → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
