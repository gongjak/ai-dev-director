# ai-dev-director — 프로젝트 지침 (L3)

> 상속: L0 `~/.claude/CLAUDE.md` · L1 `~/Developer/CLAUDE.md` · L2 `~/Developer/experiments/CLAUDE.md`.
> 원본 `digilogd-cmd/ai-dev-director`(MIT) 의 포크 `gongjak/ai-dev-director` 다. 이 기계(M1 macOS)에서 돌게 맞춘다.

```yaml
grade: prototype
```

> 마크다운 문서를 다듬을 때는 doc-simplify 스킬을 따른다.

- 가상환경은 `uv venv --python 3.12` · 시험은 `.venv/bin/python -m pytest -q`
- Claude 역할은 opus 로만 돈다(`--model opus` 강제 · Sonnet 금지)
- 공작 확인이 필요한 범주와 HEAD 범위는 `core/owner_policy.py` 가 코드로 지킨다
