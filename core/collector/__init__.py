"""웹 검색 · 수집 CLI(Collector) — 설계 docs/COLLECTOR_DESIGN.md. Director 코드가 받고, haiku 는 뽑기만 한다."""


def run_collect(order_path, out_dir, **kwargs):
    """(exit_code, summary). 실행 진입점은 core.collector.cli."""
    from core.collector.cli import run_collect as _run
    return _run(order_path, out_dir, **kwargs)
