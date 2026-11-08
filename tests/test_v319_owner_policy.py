"""포크 위임 정책 시험 — 소유자 범주 · HEAD 범위 밖은 HEAD 가 승인해도 멈춘다. 그 밖은 HEAD 가 정한다."""
import pytest

from core import autopilot, owner_policy
from core.env_utils import claude_model_args
from schemas.order import OrderSchema


class ApprovingHead:
    """무엇이든 APPROVE 하는 HEAD — 정책이 HEAD 를 이기는지 본다."""
    def __init__(self):
        self.calls = 0

    def proxy_decide(self, question, evidence, cwd=None):
        self.calls += 1
        return {"decision": "APPROVE", "reason": "looks fine"}


OWNER_CASES = [
    ("Stripe 결제 연동을 붙인다", "PAYMENT"),
    ("Integrate a paid API plan for search", "PAYMENT"),
    ("실계좌 주문을 넣는 기능", "LIVE_ACCOUNT_ORDER"),
    ("place orders on the live account", "LIVE_ACCOUNT_ORDER"),
    ("진짜 API 키를 config 에 넣는다", "REAL_CREDENTIAL"),
    ("create an account on the vendor site", "REAL_CREDENTIAL"),
    ("deploy to production after tests", "IRREVERSIBLE_EXTERNAL"),
    ("main 에 force-push 한다", "IRREVERSIBLE_EXTERNAL"),
    ("NAS 의 옛 사진 폴더를 삭제한다", "NAS_DELETE"),
    ("delete duplicates under /volume1/photo", "NAS_DELETE"),
    ("ibtravel 운영 서버 설정을 고친다", "CLIENT_PROD_WRITE"),
    ("update the ibtravel production RDS schema", "CLIENT_PROD_WRITE"),
]

SCOPE_CASES = [
    "kpaean 에 ssh 로 배포한다",
    "rsync the build to cheongbimini",
    "청비미니에 설치한다",
]

ORDINARY = [
    "Add a slugify helper with tests",
    "README 오타를 고친다",
    "refactor the CSV parser and add unit tests",
    "nasal spray dosage calculator",          # 'nas' 는 낱말 경계에서만 잡는다
]


@pytest.mark.parametrize("text,code", OWNER_CASES)
def test_owner_categories_detected(text, code):
    assert code in owner_policy.owner_only_reasons(text)


@pytest.mark.parametrize("text", SCOPE_CASES)
def test_node_deploy_out_of_scope(text):
    assert owner_policy.out_of_scope_reasons(text) == ["NODE_DEPLOY"]


@pytest.mark.parametrize("text", ORDINARY)
def test_ordinary_repo_work_not_flagged(text):
    assert owner_policy.hard_stop_reasons(text) == []


@pytest.mark.parametrize("text,code", OWNER_CASES)
def test_head_approval_cannot_override_owner_stop(text, code):
    head = ApprovingHead()
    ok, why, source = autopilot.proxy_verdict(head, "Approve?", {"scan_text": text})
    assert ok is False and source == "POLICY_HARD" and code in why
    assert head.calls == 0                   # HEAD 에 묻지도 않는다


@pytest.mark.parametrize("text", SCOPE_CASES)
def test_head_approval_cannot_widen_scope_to_nodes(text):
    ok, _, source = autopilot.proxy_verdict(ApprovingHead(), "Approve?", {"scan_text": text})
    assert ok is False and source == "POLICY_HARD"


def test_policy_fallback_without_head_escalates():
    ok, _, source = autopilot.proxy_verdict(None, "Approve?", {"scan_text": "NAS 자료를 지운다"})
    assert ok is False and source == "POLICY_HARD"


@pytest.mark.parametrize("text", ORDINARY)
def test_head_decides_everything_else(text):
    head = ApprovingHead()
    ok, _, source = autopilot.proxy_verdict(head, "Approve?", {"scan_text": text})
    assert ok is True and source == "HEAD" and head.calls == 1


def _order(goal):
    return OrderSchema(goal=goal, allowed_paths=["src/**", "tests/**"], risk_level="LOW",
                       max_files_changed=3, max_diff_lines=100)


def test_entry_gate_escalates_owner_order_even_with_approving_head():
    gate = autopilot.entry_gate(_order("ibtravel 운영 서버 DB 를 갱신한다"), None, ApprovingHead())
    assert gate["escalate"] and "OWNER_ONLY:CLIENT_PROD_WRITE" in gate["escalate"]


def test_entry_gate_escalates_node_deploy():
    gate = autopilot.entry_gate(_order("scp 로 kpaean 에 배포한다"), None, ApprovingHead())
    assert gate["escalate"] and "OUT_OF_SCOPE:NODE_DEPLOY" in gate["escalate"]


def test_entry_gate_passes_ordinary_order():
    gate = autopilot.entry_gate(_order("Add a slugify helper with tests"), None, ApprovingHead())
    assert gate["escalate"] is None


def test_stop_detail_reaches_user():
    assert autopilot.run_stop_needs_user("Scope violation: Out of HEAD scope: deploy.sh (node deployment)")
    assert autopilot.run_stop_needs_user("Scope violation: Owner-only action: x.py (NAS data deletion)")
    assert not autopilot.head_decision_only("USER_DECISION (OWNER_ONLY:NAS_DELETE)")


DIFF = """diff --git a/deploy.sh b/deploy.sh
+++ b/deploy.sh
+rsync -a dist/ kpaean:/srv/app/
+++ b/clean.py
+shutil.rmtree('/volume1/photo/old')
+++ b/db.sh
+psql -h ibtravel-prod.example -c 'update x'
+++ b/push.sh
+git push origin main --force
+++ b/ok.py
+print('ssh is a word here')
"""


def test_code_findings_flag_added_lines():
    found = owner_policy.code_findings(DIFF)
    joined = "\n".join(found)
    assert "Out of HEAD scope: deploy.sh" in joined
    assert "Owner-only action: clean.py (NAS data deletion)" in joined
    assert "Owner-only action: db.sh (client production server access)" in joined
    assert "Owner-only action: push.sh (force push)" in joined
    assert "ok.py" not in joined


def test_code_findings_scan_new_files():
    found = owner_policy.code_findings("", {"tools/sync.py": "subprocess.run(['ssh', 'nas', 'ls'])"})
    assert found and found[0].startswith("Out of HEAD scope: tools/sync.py")


def test_claude_roles_pinned_to_opus(monkeypatch):
    monkeypatch.delenv("DIRECTOR_CLAUDE_MODEL", raising=False)
    assert claude_model_args() == ["--model", "opus"]
    monkeypatch.setenv("DIRECTOR_CLAUDE_MODEL", "claude-sonnet-5-5")
    with pytest.raises(RuntimeError):
        claude_model_args()


def test_claude_command_lines_carry_model(monkeypatch):
    monkeypatch.delenv("DIRECTOR_CLAUDE_MODEL", raising=False)
    from core.research_runner import TriadResearchRunner
    cmd, _, _ = TriadResearchRunner(executables={})._command("ANTHROPIC", "/x/claude", "hi")
    assert cmd[-2:] == ["--model", "opus"]
