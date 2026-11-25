"""Bounded, explicitly referenced document input for the proposal Builder."""
import os
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree


# 폴더 참조(포크) — 한 폴더에서 넘기는 양의 한도. 문서 한도(100,000자 · 합계 200,000자)와 같은 방식으로 둔다
FOLDER_MAX_FILES = 60            # 한 폴더에서 넘기는 파일 수
FOLDER_FILE_MAX_BYTES = 100_000  # 이보다 큰 파일은 이름만 알린다
FOLDER_MAX_CHARS = 120_000       # 한 폴더에서 넘기는 글자 수
FOLDER_MAX_SCANNED = 5_000       # 이만큼 훑고도 끝나지 않으면 멈춘다 — 거대한 폴더에서 걷기가 끝나지 않는 것을 막는다
TOTAL_MAX_CHARS = 200_000        # 문서 + 폴더 전체
FOLDER_TEXT_SUFFIXES = {'.py', '.ts', '.tsx', '.js', '.jsx', '.cjs', '.mjs', '.md', '.txt', '.json', '.yaml', '.yml',
                        '.toml'}
_FOLDER_SKIP_DIRS = {'node_modules', '__pycache__', 'venv', 'dist', 'build'}   # 숨김 폴더(.git · .venv …)는 따로 뺀다
_SECRET_SUFFIXES = {'.pem', '.key', '.p12', '.pfx', '.jks', '.keystore'}
_SECRET_WORDS = ('secret', 'credential', 'token', 'private_key', 'password')
DIRECTOR_ROOT = Path(__file__).resolve().parent.parent      # Director 자신 — runs/ · data/ · config.yaml 이 여기 있다
# 내용으로 보는 비밀(4차 리뷰 C) — 이름이 평범한 설정 파일(config.yaml · settings.py)에 든 값을 막는다
_SECRET_KEYS = r'(?:secret|passw|token|credential|api[_-]?key|access[_-]?key|private[_-]?key)'
_SECRET_ALWAYS = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY|\bsk-[A-Za-z0-9_-]{1,}|\bAKIA[0-9A-Z]{12,}|\bgh[pousr]_[A-Za-z0-9]{20,}'
                            r'|\bxox[abprs]-[A-Za-z0-9-]{8,}|\b1//0[A-Za-z0-9_-]')
# 설정 파일(.json · .yaml · .toml · .txt)은 `키: 값` 만 있어도 비밀로 본다
_SECRET_IN_CONFIG = re.compile(_SECRET_KEYS + r'[\w-]*["\x27]?\s*[:=]\s*\S'
                               r'|\brefresh(?:[_-]?token)?["\x27]?\s*[:=]\s*\S', re.I)
# 코드 · 문서는 따옴표 안의 값이 붙어 있을 때만 — `def login(password: str)` · `token = next()` 는 비밀이 아니다
_SECRET_IN_CODE = re.compile(_SECRET_KEYS + r'[\w-]*["\x27]?\s*[:=]\s*["\x27][^"\x27\s]{3,}["\x27]', re.I)
_CONFIG_SUFFIXES = {'.json', '.yaml', '.yml', '.toml', '.txt'}


def project_roots(worktree_path):
    """작업 폴더(worktree)와 그것을 잘라 낸 프로젝트. 이 안의 파일은 빌더가 이미 소스로 받는다."""
    from core.env_utils import run_capture
    roots = [Path(worktree_path).resolve()]
    try:
        res = run_capture(['git', 'rev-parse', '--git-common-dir'], cwd=str(roots[0]), timeout=10)
        common = Path(res.stdout.strip())
        if res.returncode == 0 and res.stdout.strip():
            common = (common if common.is_absolute() else roots[0] / common).resolve()
            if common.name == '.git':
                roots.append(common.parent)
    except Exception:
        pass
    return roots


def reference_roots(worktree_path):
    """Folders a referenced document may live in: the worktree, the project it was cut from, and the folders the
    operator listed under safety.reference_roots in config.yaml."""
    from core.env_utils import root_config
    roots = project_roots(worktree_path)
    for extra in (root_config().get('safety') or {}).get('reference_roots') or []:
        try:
            # 설정에 `~/Developer/oss` 처럼 적어도 되게 홈을 편다
            roots.append(Path(os.path.expanduser(str(extra))).resolve())
        except OSError:
            continue
    return roots


def denied_roots():
    """참조 루트에서 늘 빼는 폴더(4차 리뷰 B) — Director 자신과 config 의 solomon.denied.
    reference_roots 에 `~/Developer/experiments` 처럼 Director 를 품은 폴더가 있어도 실행 증거 · 설정이 나가지 않는다."""
    from core.env_utils import root_config
    out = [Path(DIRECTOR_ROOT).resolve()]
    for extra in (root_config().get('solomon') or {}).get('denied') or []:
        try:
            out.append(Path(os.path.expanduser(str(extra))).resolve())
        except OSError:
            continue
    return out


def _under(target, roots):
    """target(이미 resolve 한 경로)을 품는 루트 가운데 가장 깊은 것. 없으면 None."""
    best = None
    for root in roots or []:
        root = Path(root)
        if target == root or root in target.parents:
            if best is None or len(root.parts) > len(best.parts):
                best = root
    return best


def _inside(path, roots, denied=None, project=None):
    """path 의 실제 위치가 roots 안인가. denied 안이면 아니다 — 다만 프로젝트 자신(project) 안은 denied 와 무관하게 허용한다."""
    target = path.resolve()
    if _under(target, [Path(r).resolve() for r in project or []]) is not None:
        return True
    if _under(target, denied) is not None:
        return False
    return _under(target, roots) is not None


def _is_denied(path, denied, project):
    target = path.resolve()
    return (_under(target, denied) is not None
            and _under(target, [Path(r).resolve() for r in project or []]) is None)


def _expand_home(value):
    """`~/…` 만 홈으로 편다. `~남의이름/…` 은 펴지 않는다 — 다른 사용자의 홈을 가리키게 두지 않는다."""
    value = str(value)
    return os.path.expanduser(value) if value == '~' or value.startswith('~/') else value


def _secret_like(rel):
    """비밀일 수 있는 이름인가 — `.env*` · `*.pem` · 이름에 key 가 든 것 · 경로에 secret/credential/token 이 든 것."""
    name = Path(rel).name.lower()
    return (name.startswith('.env') or Path(name).suffix in _SECRET_SUFFIXES or 'key' in name or name.startswith('id_rsa')
            or any(word in rel.lower() for word in _SECRET_WORDS))


def _secret_component(part):
    """폴더 이름 하나가 비밀 자리로 보이는가 — 숨김(`.secrets` · `.ssh`) 이거나 secret/credential/token… 이 들었다."""
    part = part.lower()
    return part.startswith('.') or any(word in part for word in _SECRET_WORDS)


def _secret_location(path, roots, is_file=False):
    """경로 자신이 비밀 자리인가(4차 리뷰 C) — 스냅샷은 폴더 「안쪽」 상대경로만 봐서 `…/secrets` 를 바로 가리키면 빠져나갔다.
    허용 루트 아래의 구성요소만 본다. 루트 위쪽(임시 폴더 이름 등)의 낱말로 전부를 가리지 않는다."""
    target = path.resolve()
    root = _under(target, [Path(r).resolve() for r in roots or []])
    parts = list(target.relative_to(root).parts) if root is not None else [target.name]
    if is_file and parts:
        # 낱개 문서는 이름을 사람이 골라 적은 것이다 — 'key' 는 낱말로 떨어져 있을 때만 본다(keyboard.md 는 비밀이 아니다)
        name = parts[-1].lower()
        if (name.startswith(('.env', 'id_rsa')) or Path(name).suffix in _SECRET_SUFFIXES
                or any(word in name for word in _SECRET_WORDS)
                or re.search(r'(?:^|[^a-z])(?:api|access|private)?[_-]?keys?(?:[^a-z]|$)', name)):
            return True
        parts = parts[:-1]
    return any(_secret_component(part) for part in parts)


def _secret_content(suffix, content):
    """내용이 비밀로 보이는가 — 개인키 머리글 · 알려진 토큰 꼴 · `password: 값` 같은 대입."""
    if _SECRET_ALWAYS.search(content):
        return True
    return bool((_SECRET_IN_CONFIG if suffix in _CONFIG_SUFFIXES else _SECRET_IN_CODE).search(content))


def _relation(folder, others):
    """folder 가 others 중 하나와 같거나 그 안이면 'inside', 그 위(조상)면 'above', 무관하면 None."""
    target = folder.resolve()
    for other in others or []:
        other = Path(other).resolve()
        if target == other or other in target.parents:
            return 'inside'
        if target in other.parents:
            return 'above'
    return None


def _folder_snapshot(folder, roots, budget, denied=None, project=None):
    """폴더 하나의 읽기 전용 스냅샷 — 글 소스 파일만, 정렬한 순서로, 한도에 닿으면 거기서 자른다(예외를 내지 않는다).
    돌려주는 것은 ({파일 절대경로: 내용}, 폴더 안내문). 참조 폴더에는 아무것도 쓰지 않는다."""
    files, omitted, hidden, used, scanned, cut, secret = {}, [], 0, 0, 0, False, []
    for directory, dirs, names in os.walk(folder, followlinks=False):
        dirs[:] = sorted(d for d in dirs if not d.startswith('.') and d not in _FOLDER_SKIP_DIRS
                         and not (Path(directory) / d).is_symlink()
                         # Director 자신 · solomon.denied 는 윗폴더를 참조해도 걸어 들어가지 않는다
                         and not (denied and _is_denied(Path(directory) / d, denied, project)))
        for name in sorted(names):
            scanned += 1
            if scanned > FOLDER_MAX_SCANNED:
                cut = True
                break
            path = Path(directory) / name
            rel = path.relative_to(folder).as_posix()
            if name.startswith('.') or path.suffix.lower() not in FOLDER_TEXT_SUFFIXES:
                continue
            if _secret_like(rel):
                hidden += 1
                continue
            try:
                # 심볼릭 링크는 따라가지 않고, 풀어 본 실제 위치가 허용 루트 안인지 파일마다 다시 본다
                if path.is_symlink() or not path.is_file() or (roots is not None
                                                               and not _inside(path, roots, denied, project)):
                    continue
                if path.stat().st_size > FOLDER_FILE_MAX_BYTES or len(files) >= FOLDER_MAX_FILES:
                    omitted.append(rel)
                    continue
                content = path.read_text(encoding='utf-8')
            except (OSError, UnicodeDecodeError):
                continue                      # 읽을 수 없거나 글이 아닌 파일(바이너리)
            if '\x00' in content:
                continue
            if _secret_content(path.suffix.lower(), content):
                secret.append(rel)            # 이름은 평범하지만 내용이 비밀로 보인다 — 내용은 넘기지 않고 이름만 알린다
                continue
            if used + len(content) > budget:
                budget = used                 # 한도에 닿았다 — 뒤의 파일은 크기와 무관하게 이름만 남긴다
                omitted.append(rel)
                continue
            files[str(path)] = content
            used += len(content)
        if cut:
            break
    note = [f'참조 폴더 {folder} 의 읽기 전용 스냅샷이다. 이 폴더에는 쓰지 않는다.',
            f'넘긴 파일 {len(files)}개 (글 소스만 · 숨김 폴더 · node_modules · 바이너리는 뺐다).']
    if hidden:
        note.append(f'비밀일 수 있는 이름이라 뺀 파일 {hidden}개.')
    if secret:
        note.append(f'비밀로 보이는 내용(키 · 암호 · 토큰 대입)이 있어 뺀 파일 {len(secret)}개: ' + ', '.join(secret[:100]))
    if omitted:
        note.append(f'한도 때문에 내용 없이 이름만 남긴 파일 {len(omitted)}개: ' + ', '.join(omitted[:100]))
    if cut:
        note.append(f'파일 {FOLDER_MAX_SCANNED}개를 훑고 멈췄다 — 폴더의 뒷부분은 보지 않았다.')
    return files, '\n'.join(note)


_PATH_START = r'(?<![A-Za-z0-9:/.~\\-])'          # 낱말 첫머리 — 앞에 글자 · ':' · '/' · '.' · '~' 이 붙으면 경로의 시작이 아니다
_DOC_TAIL = r'(?![/\\])[^\r\n"<>|`=*?\x27()]*?\.(?:docx|md|txt)(?=[\s"\x27`)\],;:]|$)'


def referenced_documents(order, roots=None, project_roots=None):
    """roots=None keeps the unrestricted behaviour for direct callers; the Builder always passes reference_roots()
    (2026-11-20, public review: any absolute .md/.txt/.docx named in an ORDER was read and sent to the provider).

    포크(2026-11-25): `~/…` 로 쓴 경로를 홈으로 펴서 알아본다. ORDER 가 폴더를 가리키면 그 폴더의 한도가 걸린 읽기 전용
    스냅샷을 파일마다 한 항목으로 넘긴다(`폴더경로/` 항목은 안내문). project_roots 는 빌더가 이미 소스로 받는 폴더다 —
    그 안팎을 가리키는 폴더 표기는 참조로 치지 않는다.

    4차 리뷰(2026-11-25) — 폴더는 goal 과 reference_paths 에서만 알아본다. context 에는 수리 라운드의 pytest 출력 · 리뷰
    글이 실리므로(임시 폴더 · `/tmp`) 거기 나온 폴더로 실행을 죽이거나 스냅샷을 내보내지 않는다. goal 에 이름만 나온
    폴더가 허용 루트 밖 · 심볼릭 링크 · 거부 폴더 · 비밀 자리면 조용히 건너뛴다. 명시한 참조(reference_paths)는 그대로
    닫힌 쪽으로 멈춘다. Director 자신과 solomon.denied 는 어느 쪽이든 참조가 되지 않는다."""
    text = '\n'.join([order.goal, order.context or '', *order.acceptance_criteria])
    denied = denied_roots() if roots is not None else None
    explicit = [_expand_home(p) for p in getattr(order, 'reference_paths', [])]
    found = []
    # A drive letter that starts a token (not the 'p' of 'http://', not a URL scheme '://') begins a document path.
    # A path never contains = * ? ' ( ): a sentence such as "E:\proj = a game ... see notes.md" is prose, not one path.
    found += re.findall(r'(?<![A-Za-z0-9])[A-Za-z]:[/\\](?![/\\])[^\r\n"<>|`=*?\x27()]*?\.(?:docx|md|txt)(?=[\s"\x27`)\],;:]|$)', text, re.I)
    tokens = []
    if os.name != 'nt':
        # macOS/Linux 절대경로(/...)도 문서 경로다. 낱말 첫머리의 '/' 만 본다 — 'http://h:1/v1' 의 '/v1',
        # 'docs/SPEC.md' 의 '/SPEC.md' 처럼 앞에 글자 · ':' · '/' · '.' 이 붙은 것은 경로의 시작이 아니다
        found += re.findall(_PATH_START + '/' + _DOC_TAIL, text, re.I)
        # 홈을 줄여 쓴 '~/...' 도 같다. 낱말 첫머리의 '~/' 만 본다 — 'a~/x.md' · '~남/x.md' 는 아니다
        found += [_expand_home(p) for p in re.findall(_PATH_START + '~/' + _DOC_TAIL, text, re.I)]
        # 폴더 후보 — 공백 없는 '/...' · '~/...' 낱말. 실제로 있는 폴더일 때만 참조로 친다('/status' · '/api/v1' 은 아니다)
        tokens = re.findall(_PATH_START + r'~?/(?![/\\])[^\s"<>|`=*?\x27()\[\]{},;]+', text)
    goal_tokens = set(re.findall(_PATH_START + r'~?/(?![/\\])[^\s"<>|`=*?\x27()\[\]{},;]+', order.goal or '')) \
        if os.name != 'nt' else set()
    folders = [(Path(p), True) for p in dict.fromkeys(explicit) if os.path.isdir(p)]
    paths = [p for p in explicit if not os.path.isdir(p)]
    named = set()
    if roots is not None:               # 글 속 폴더 표기는 허용 루트가 주어졌을 때만 본다(직접 부르는 쪽은 예전 그대로)
        for raw in dict.fromkeys(tokens):
            token = _expand_home(raw.rstrip('.:'))
            if not os.path.isdir(token):
                # '~/oss/map-hub를' 처럼 조사가 붙은 표기 — 뒤의 한글 등 비ASCII 를 떼고 한 번 더 본다
                token = re.sub(r'[^\x00-\x7f]+$', '', token).rstrip('.:')
            if len(token) > 1 and os.path.isdir(token):
                named.add(token.rstrip('/'))      # 문서 정규식의 오인을 막는 데는 글 전체의 폴더 표기를 쓴다
                if raw in goal_tokens:            # 스냅샷은 goal 에 나온 것만
                    folders.append((Path(token.rstrip('/')), False))
    for value in found:
        # '/a/hub 폴더를 보고 README.md 를 쓴다' — 문서 정규식은 공백을 넘어 '.md' 까지 한 경로로 잡는다. 없는 파일이고
        # 첫 낱말이 위에서 폴더로 알아본 것이면 문서 경로가 아니라 폴더 표기다
        first = value.split()[0].rstrip('.:/') if value.split() else value
        if not os.path.exists(value) and (first in named or re.sub(r'[^\x00-\x7f]+$', '', first).rstrip('.:/') in named):
            continue
        paths.append(value)
    documents = {}
    for value in dict.fromkeys(paths):
        path = Path(value)
        try:
            if not path.is_absolute() or path.is_symlink():
                raise ValueError('Reference must be an absolute regular file')
            if roots is not None and not _inside(path, roots):
                raise ValueError('outside the project and safety.reference_roots; add its folder to '
                                 'safety.reference_roots in config.yaml to allow it')
            if roots is not None and _is_denied(path, denied, project_roots):
                raise ValueError('inside the Director itself or a solomon.denied folder; never a reference')
            if roots is not None and _secret_location(path, list(roots) + list(project_roots or []), is_file=True):
                raise ValueError('name or folder looks like a secret (key/token/password/credential/hidden folder); '
                                 'not sent to the provider')
            if path.stat().st_size > 10_000_000:
                raise ValueError('Reference exceeds 10 MB')
            if path.suffix.lower() == '.docx':
                with zipfile.ZipFile(path) as archive:
                    info = archive.getinfo('word/document.xml')
                    if info.file_size > 5_000_000:
                        raise ValueError('Document XML exceeds limit')
                    root = ElementTree.fromstring(archive.read(info))
                ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
                content = '\n'.join(''.join(p.itertext()) for p in root.findall('.//w:p', ns))
            elif path.suffix.lower() in {'.txt', '.md'}:
                content = path.read_text(encoding='utf-8')
            else:
                raise ValueError('Unsupported reference format')
            if len(content) > 100_000 or sum(len(v) for v in documents.values()) + len(content) > TOTAL_MAX_CHARS:
                raise ValueError('Reference text exceeds context budget')
            documents[str(path)] = content
        except (OSError, ValueError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
            raise ValueError(f'REFERENCE_INPUT_UNAVAILABLE: {value}: {exc}') from exc
    seen = set()
    for folder, was_explicit in folders:
        try:
            if not folder.is_absolute() or folder.is_symlink():
                if not was_explicit:
                    continue                  # goal 에 이름만 나온 것(`/tmp` 는 macOS 에서 심볼릭 링크다) — 건너뛴다
                raise ValueError('Reference folder must be an absolute real folder (not a symlink)')
            resolved = folder.resolve()
            relation = _relation(folder, project_roots)
            # 프로젝트 자신 · 그 하위는 빌더가 이미 소스로 받는다. 글 속에 나온 프로젝트의 윗폴더도 참조가 아니다
            if resolved in seen or relation == 'inside' or (relation == 'above' and not was_explicit):
                continue
            problem = None
            if roots is not None and not _inside(folder, roots):
                problem = ('folder outside the project and safety.reference_roots; add it to '
                           'safety.reference_roots in config.yaml to allow it')
            elif roots is not None and _is_denied(folder, denied, project_roots):
                problem = 'folder is the Director itself or a solomon.denied folder; never a reference'
            elif _secret_location(folder, roots):
                problem = 'folder path looks like a secret store (secret/credential/token/password or hidden folder)'
            if problem:
                if not was_explicit:
                    continue                  # goal 에 이름만 나온 것 — 실행을 죽이지 않고 건너뛴다
                raise ValueError(problem)
            seen.add(resolved)
            budget = min(FOLDER_MAX_CHARS, TOTAL_MAX_CHARS - sum(len(v) for v in documents.values()))
            files, note = _folder_snapshot(folder, roots, max(budget, 0), denied, project_roots)
            documents[str(folder) + '/'] = note
            for key, content in files.items():
                documents.setdefault(key, content)
        except (OSError, ValueError) as exc:
            raise ValueError(f'REFERENCE_INPUT_UNAVAILABLE: {folder}: {exc}') from exc
    return documents
