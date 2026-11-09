"""Collector 시험용 가짜 — 네트워크 · 실제 CLI 없이 돈다."""
import json
import os
import stat
from urllib.parse import urlsplit

from core.collector.netfetch import RawResponse, TransportError

FAKE_DNS = {"example.com": ["93.184.216.34"], "www.example.com": ["93.184.216.34"], "shop.example.org": ["93.184.216.35"],
            "evil.test": ["127.0.0.1"], "tail.test": ["100.100.1.2"], "mixed.test": ["93.184.216.34", "10.0.0.5"],
            "blog.naver.com": ["93.184.216.36"]}


def fake_resolver(host, port):
    if host not in FAKE_DNS:
        raise OSError(f"no such host {host}")
    return FAKE_DNS[host]


class FakeTransport:
    """{url: (status, headers, body)} — robots.txt 가 없으면 404. body 가 Exception 이면 그것을 던진다."""

    def __init__(self, routes):
        self.routes = dict(routes)
        self.requests = []

    def request(self, *, scheme, host, port, ip, path, headers, timeout, max_bytes):
        url = f"{scheme}://{host}{path}"
        self.requests.append({"url": url, "ip": ip, "headers": headers})
        status, hdrs, body = self.routes.get(url, (404, {}, b"") if path == "/robots.txt" else (404, {}, b"nf"))
        if isinstance(body, Exception):
            raise TransportError(str(body))
        if isinstance(body, str):
            body = body.encode("utf-8")
        truncated = len(body) > max_bytes
        return RawResponse(status, {k.lower(): v for k, v in hdrs.items()}, body[:max_bytes], truncated)


HTML = {"Content-Type": "text/html; charset=utf-8"}


def make_stub(tmp_path, name, responses, write_file=False, exit_code=0, stderr=""):
    """claude 대신 쓰는 셸 스크립트. 호출마다 responses 의 다음 값을 `--output-format json` 형태로 낸다.
    stdin 은 tmp_path/prompts/<n>.txt 에 남긴다(작업 폴더 밖). 인자는 args_<n>.txt."""
    d = tmp_path / f"stub_{name}"
    d.mkdir()
    (d / "prompts").mkdir()
    for i, r in enumerate(responses, 1):
        text = r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
        (d / f"resp_{i}.json").write_text(json.dumps({"type": "result", "result": text}, ensure_ascii=False),
                                          encoding="utf-8")
    script = d / "collector-stub"
    script.write_text(
        "#!/bin/sh\n"
        f'D="{d}"\n'
        'N=$(ls "$D/prompts" | wc -l | tr -d " "); N=$((N+1))\n'
        'cat > "$D/prompts/$N.txt"\n'
        'printf "%s\\n" "$@" > "$D/args_$N.txt"\n'
        + ("touch ./leaked.txt\n" if write_file else "")
        + (f'echo "{stderr}" >&2\n' if stderr else "")
        + 'if [ -f "$D/resp_$N.json" ]; then cat "$D/resp_$N.json"; fi\n'
        + f"exit {exit_code}\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return str(script), d
