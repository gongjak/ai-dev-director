---
title: "포크 변경 기록 — gongjak/ai-dev-director"
date: 2026-11-09
modified: 2026-11-09
tags:
  - type/changelog
  - scope/dev
source: claude
---

# 포크 변경 기록

원본 `digilogd-cmd/ai-dev-director`(MIT)에서 갈라진 뒤 **이 포크에서만 바꾼 것**을 모두 적는다.
원본을 다시 받아 합칠 때 이 표가 무엇을 지켜야 하는지 알려 준다. 기준점은 원본 `d2541ad`(v3.1.8)이다.

> 규칙 — 포크에서 코드를 고치면 **같은 커밋에서 이 파일에 한 절을 더한다.** 무엇을 · 왜 · 어느 파일 · 시험 · 리뷰를 적는다.

## 한눈에

| 날짜 (KST) | 커밋 | 무엇 |
|---|---|---|
| 2026-11-08 | `e5922c5` | 위임 정책을 코드로 고정(`core/owner_policy.py`) · Claude 역할을 opus 로 묶는다 |
| 2026-11-08 | `a033a0b` | macOS 에서 실패하던 시험 16건의 원인을 고친다 |
| 2026-11-08 | `e6e1ef5` | 콘솔을 `companion.json` 의 host(Tailscale 주소)에 묶는다 · `0.0.0.0` 거부 · 시작 스크립트는 `.venv` 파이썬 |
| 2026-11-09 | `5384978` | 빌더 교체 순서를 설정으로 고정 — agy 를 못 쓰면 claude → codex |
| 2026-11-09 | `374104e` | 적용 관문 결함 — HEAD 프록시가 P2 만 남기고 PASS 한 결과도 적용할 수 있게 한다 |

## 2026-11-09 — 빌더 교체 순서 (agy → claude → codex)

- **왜** — agy 가 한도(`RESOURCE_EXHAUSTED`, 11-14 10:40 KST 풀림)에 걸렸을 때 claude 다음 codex 로 넘기라는 지시. 기존 `pick_alternative()` 는 기본 순서가 claude → codex 였지만, 「다른 역할이 쓰는 제공자를 피한다(avoid)」 정렬 때문에 역할 배치에 따라 codex 가 먼저 골라질 수 있었다
- **바꾼 것**
  - `config.yaml` — `builder.failover_order: [claude, codex]`
  - `core/autopilot.py` `pick_alternative(..., order=None)` — `order` 를 주면 그 순서대로 「쓸 수 있고 시도하지 않은」 첫 제공자를 고른다. avoid 정렬을 쓰지 않는다. 별칭 `codex` · `agy` · `google` · `anthropic` 도 받는다
  - `core/orchestrator.py` — 실행 중 교체 `_swap_agent()` 와 시작 때 배정 `_autonomous_role_assignment()` 둘 다 `config[role].failover_order` 를 넘긴다
- **시험** — `tests/test_builder_failover_order.py` 7건. 시작 때 배정 시험은 수정을 빼면 실패하는 것을 확인했다. 전체 659 통과
- **리뷰** — codex: 시작 때 배정 경로 누락(HIGH)을 지적 → 반영. agy 는 한도로 못 받았다
- **주의** — 실행 중인 디렉터 프로세스에는 다시 띄운 뒤에 적용된다

## 2026-11-09 — 적용 관문 결함 (P2 만 남은 PASS 를 적용 못 함)

- **증상** — 스테이크 지도 미션 3단계(`RUN-261109143600-…`)가 PASS 인데 「원본에 적용」이 6번 모두 `RESULT_APPLY_BLOCKED: final_evidence is missing or not fully PASS` 로 막혔다
- **원인** — 오케스트레이터는 HEAD 프록시 모드에서 「리뷰가 `changes_required` 지만 P0 · P1 이 없고 P2 만 있다」면 PASS 로 끝내고 P2 를 후속 일로 적는다(`core/orchestrator.py` 「only P2 (minor) review findings remain」). 그런데 적용 관문 `preconditions()` · `tree_preconditions()` 는 `final_evidence.reviewer_verdict == "pass"` 만 받았다. 두 규칙이 어긋나 **오케스트레이터가 통과시킨 결과를 관문이 언제나 막았다**
- **바꾼 것** — `core/result_manager.py` 에 `_review_accepted(run_id, ev)` 를 두고 두 관문이 쓴다. 받는 경우는 둘뿐이다
  1. 리뷰 판정이 `pass`
  2. 리뷰 판정이 `changes_required` 이고, `review.json` 에 P0 · P1 이 없고 P2 가 있으며, `decision.json` 의 결정이 `PASS`
  - 그 밖(판정 없음 · 파일 없음 · 깨짐 · P0/P1 있음 · P2 없음 · 결정 FAIL)은 받지 않는다(fail-closed). 실행 상태 PASS · 검증 · 기준선 · 회귀 · 경로 격리 조건은 그대로다
- **확인** — 고친 뒤 `RUN-261109143600-…` 의 `tree_preconditions` 가 `ok=True` · 파일 22개. 실제 적용은 하지 않았다(사람이 버튼으로 한다)
- **시험** — `tests/test_apply_gate_minor_only.py` 9건. 전체 668 통과
- **리뷰** — codex · agy 모두 한도(codex 11-12 16:27 KST 풀림). opus 서브에이전트 독립 리뷰로 대신했다 — 결과는 아래 「리뷰 결과」
- **주의** — 콘솔(`companion_server.py` · 8081)을 다시 띄워야 버튼에 적용된다

### 리뷰 결과

- opus 독립 리뷰(`code-reviewer`) — **APPROVE.** 확인한 것: `review.json` · `decision.json` · `final_evidence.json` 은 마지막 수리 라운드의 같은 판정에서 나온다(`_finalize` 에서만 쓴다) · `decision.json` 은 오케스트레이터가 결정을 바꾼 뒤의 최종값이다 · P0 · P1 · 삭제 경고 실행은 결정이 FAIL/HUMAN_REQUIRED 로 바뀌어 여전히 막힌다
- 낮은 등급 지적 둘 반영 — ① 오케스트레이터의 삭제 경고 조건(p1 · p2 에 remove/delet)을 관문에서도 다시 본다 ② 깨진 JSON 은 예외 대신 거부한다. 시험 2건 추가(모두 11건) · 전체 670 통과

## 2026-11-09 관찰 — 아직 고치지 않은 것

- **빌더는 웹을 읽지 못한다** — `config.yaml` `builder.mode: proposal` · `agents/proposal_builder.py` 지시문 「Do not use tools」. 자료 수집이 필요한 미션은 데이터 없이 코드만 고치다 연속 실패로 멈춘다(스테이크 지도 4단계 `ENV_NO_WEB`). 수집 단계를 따로 두는 설계를 검토 중이다
- **미션 종료 때 자동 적용이 `duplicate` 로 끝난다** — 감사 기록으로 잰 경위: 16:02 KST 미션 종료 때 `auto-MSN-…` 요청이 위 적용 관문 결함으로 `RESULT_APPLY_BLOCKED`. 16:44 미션이 다시 끝나며(`_finish`) `_auto_apply` 가 **같은 요청 번호**를 다시 써서 메모리의 중복 방지(`_RECENT_APPLY_REQUESTS`)에 걸렸다. 관문 결함이 풀렸으니 새 미션에서는 첫 호출이 적용된다. 다만 막힌 적용을 다시 시도할 때도 같은 번호를 쓰는 점은 남아 있다 — 고치려면 요청 번호에 시도 시각을 붙인다(아직 고치지 않음)
