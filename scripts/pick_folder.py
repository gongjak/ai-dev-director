"""Native folder dialog for the 8081 console. A browser never reveals a folder's absolute path, so the local
companion server runs this helper and reads the choice from stdout: one JSON line {"path": "..."} ("" = cancelled).
macOS 는 osascript(choose folder) 기본 대화상자, 그 밖은 tkinter 를 쓴다."""
import json
import subprocess
import sys


def pick_macos(initial: str) -> str:
    # 경로는 argv 로 넘겨 AppleScript 문자열 주입을 막는다
    script = ('on run argv\n'
              '  set p to "AI DEV DIRECTOR - 작업할 폴더 선택"\n'
              '  tell application "System Events" to activate\n'
              '  try\n'
              '    if (item 1 of argv) is not "" then\n'
              '      return POSIX path of (choose folder with prompt p default location (POSIX file (item 1 of argv)))\n'
              '    end if\n'
              '    return POSIX path of (choose folder with prompt p)\n'
              '  on error number -128\n'
              '    return ""\n'
              '  end try\n'
              'end run')
    out = subprocess.run(["osascript", "-e", script, initial], capture_output=True, text=True)
    if out.returncode != 0:
        raise OSError(out.stderr.strip())
    chosen = out.stdout.strip()
    return chosen.rstrip("/") if len(chosen) > 1 else chosen


def pick_tk(initial: str) -> str:
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)          # the dialog must not open behind the browser
    root.update()
    chosen = filedialog.askdirectory(parent=root, initialdir=initial or None, mustexist=True,
                                     title="AI DEV DIRECTOR - 작업할 폴더 선택")
    root.destroy()
    return chosen or ""


def main() -> int:
    initial = sys.argv[1] if len(sys.argv) > 1 else ""
    try:
        chosen = pick_macos(initial) if sys.platform == "darwin" else pick_tk(initial)
    except (ImportError, OSError) as e:        # e.g. python without python-tk: type the path in the console instead
        sys.stderr.write(f"folder dialog unavailable: {e}\n")
        return 1
    sys.stdout.buffer.write(json.dumps({"path": chosen}).encode("ascii"))   # ensure_ascii: safe on any codepage
    return 0


if __name__ == "__main__":
    sys.exit(main())
