#!/usr/bin/env python3
"""Self-driving screen tour of EarlyAlert, recorded to a .mov.

Drives Google Chrome through the browser scenes of the demo script on a timer
while macOS records the screen, so the on-screen actions happen on cue and you
only have to narrate over the result.

    .venv/bin/python tools/demo_tour.py --check     # verify everything first
    .venv/bin/python tools/demo_tour.py --reset     # clear the demo flags
    .venv/bin/python tools/demo_tour.py --dry-run   # walk it without recording
    .venv/bin/python tools/demo_tour.py             # record

Covers scenes 3 to 9. Scenes 1, 2, 10 and 11 are the talking head, the terminal
and the result figures; record those separately and cut them in.

Two things have to be true before this works, both one-time:

  1. Chrome must allow JavaScript from Apple Events.
     Chrome menu: View > Developer > Allow JavaScript from Apple Events.
  2. Whatever runs this needs Screen Recording permission.
     System Settings > Privacy & Security > Screen Recording. macOS will
     prompt the first time; grant it, then run this again.

Running it prints a YouTube chapter list with real timecodes at the end.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

BASE = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent
CARDS = ROOT / "cards"

# Fixed in the demo data. See the demo script for why each was chosen.
PRESENTATION_DDD = 12          # DDD/2014B, 972 scored students
PRESENTATION_AAA_2014 = 2      # AAA/2014J, where subgroup suppression fires
PRESENTATION_UNASSIGNED = 17   # FFF/2014J, not assigned to advisor1
STUDENT_FLAGGABLE = 16533      # id 415151, top of DDD/2014B, still registered
STUDENT_NO_DATA = 126          # id 292923, registration but no clickstream
STUDENT_WITHDRAWN = 17268      # id 629632, unregistered on day 171

PASSWORD = "earlyalert-dev"


# --------------------------------------------------------------------------
# Steps
# --------------------------------------------------------------------------

@dataclass
class Step:
    """One beat of the tour. `hold` is how long it stays on screen."""

    hold: float
    kind: str          # card | go | js | chapter
    payload: str = ""
    note: str = ""     # printed while running, so you can follow along
    chapter: str = ""  # non-empty starts a new YouTube chapter here


def card(name: str, title: str, subtitle: str, hold: float = 3.5,
         chapter: str = "") -> Step:
    return Step(hold, "card", json.dumps([name, title, subtitle]),
                note=f"card: {title}", chapter=chapter)


def go(path: str, hold: float, note: str, chapter: str = "") -> Step:
    return Step(hold, "go", BASE + path, note=note, chapter=chapter)


def js(code: str, hold: float, note: str) -> Step:
    return Step(hold, "js", code, note=note)


def scroll(y: int, hold: float, note: str) -> Step:
    return js(f"window.scrollTo({{top:{y},behavior:'smooth'}});", hold, note)


LOGIN = """
document.querySelector('input[name=username]').value = {u};
document.querySelector('input[name=password]').value = {p};
document.querySelector('form').submit();
"""


def login(username: str, password: str, hold: float, note: str) -> Step:
    return js(LOGIN.format(u=json.dumps(username), p=json.dumps(password)),
              hold, note)


def build_steps() -> list[Step]:
    today = time.strftime("%Y-%m-%d")
    tomorrow = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))

    return [
        card("title", "EarlyAlert",
             "Predicting student withdrawal, early enough to act",
             hold=5, chapter="Opening"),

        # --- Scene 3: roles and authorization --------------------------------
        card("s3", "Three roles, enforced",
             "Authorization in one service layer, not per view",
             chapter="Roles and authorization"),
        go("/logout/", 2, "log out of any existing session"),
        go("/presentations/", 4, "protected page with no session bounces to login"),
        login("advisor1", "wrong-password", 5,
              "wrong password: 'Username or password not recognized.'"),
        login("nosuchuser", "wrong-password", 5,
              "unknown username: the identical message"),
        login("advisor1", PASSWORD, 5, "correct credentials"),
        go("/presentations/", 6,
           "the selector lists only assigned presentations, 3 of 22"),
        go(f"/presentations/{PRESENTATION_UNASSIGNED}/ranking/", 9,
           "URL tampering to an unassigned presentation -> 403, nothing disclosed"),

        # --- Scene 4: the ranking -------------------------------------------
        card("s4", "The ranking", "972 scored students, read from stored scores",
             chapter="The ranking"),
        go(f"/presentations/{PRESENTATION_DDD}/ranking/?horizon=8", 9,
           "ranking at week 8: probability, risk band, leading reason, flag"),
        scroll(700, 7, "scroll the ranking"),
        scroll(0, 3, "back to the top"),
        go(f"/presentations/{PRESENTATION_DDD}/ranking/?horizon=4", 6,
           "week 4: a different model's stored scores"),
        go(f"/presentations/{PRESENTATION_DDD}/ranking/?horizon=12", 6,
           "week 12"),
        go(f"/presentations/{PRESENTATION_DDD}/ranking/?horizon=8&page=2", 5,
           "page 2 - bands continue across pages"),

        # --- Scene 5: one student -------------------------------------------
        card("s5", "One student, explained",
             "The score, and the behaviour behind it",
             chapter="Student detail"),
        go(f"/students/{STUDENT_FLAGGABLE}/?horizon=8", 9,
           "detail: demographics, probability, band, three reasons"),
        scroll(750, 8, "the weekly engagement history"),
        scroll(0, 2, "back to the top"),
        go(f"/students/{STUDENT_NO_DATA}/", 9,
           "a registration with no clickstream: no probability is shown"),

        # --- Scene 6: the intervention workflow ------------------------------
        card("s6", "The intervention workflow",
             "Flag, contact, outcome - and what each closure means after",
             chapter="Intervention workflow"),
        go(f"/students/{STUDENT_FLAGGABLE}/?horizon=8", 4, "back to the student"),
        scroll(2400, 4, "down to the flag control"),
        js("""
           var f = document.querySelector('form[action*="/flag/"]');
           f.querySelector('textarea[name=reason]').value =
             'No VLE activity for three weeks and two assessments unsubmitted.';
           setTimeout(function(){ f.submit(); }, 1200);
           """, 7, "raise a flag with a reason"),
        js("""
           var f = document.querySelector('form textarea[name=note]').form;
           f.querySelector('textarea[name=note]').value =
             'Left a voicemail on the number on file.';
           setTimeout(function(){ f.submit(); }, 1200);
           """, 7, "add a note to the open flag"),
        js(f"""
           var f = document.querySelector('select[name=intervention_type]').form;
           f.querySelector('select[name=intervention_type]').value = 'phone';
           f.querySelector('input[name=date]').value = {json.dumps(tomorrow)};
           setTimeout(function(){{ f.submit(); }}, 1400);
           """, 8, "an intervention dated tomorrow: refused"),
        js(f"""
           var f = document.querySelector('select[name=intervention_type]').form;
           f.querySelector('select[name=intervention_type]').value = 'phone';
           f.querySelector('input[name=date]').value = {json.dumps(today)};
           f.querySelector('textarea[name=notes]').value =
             'Spoke briefly. Agreed to submit TMA02 by Friday.';
           setTimeout(function(){{ f.submit(); }}, 1400);
           """, 7, "record the intervention"),
        js("""
           var f = document.querySelector('form[action*="/outcome/"]');
           f.querySelector('select[name=outcome]').value = 're_engaged';
           setTimeout(function(){ f.submit(); }, 1400);
           """, 7, "record the outcome: the flag closes as resolved"),
        go(f"/students/{STUDENT_FLAGGABLE}/?horizon=8", 4, "back to the student"),
        scroll(2400, 7, "closed as resolved: the flag control is gone"),
        go(f"/students/{STUDENT_WITHDRAWN}/?horizon=8", 4, "a withdrawn student"),
        scroll(2400, 7,
               "already unregistered: control disabled, withdrawal day shown"),

        # --- Scene 7: the dashboard ------------------------------------------
        card("s7", "Small cells are suppressed",
             "A group of fewer than 20 is named, and not described",
             chapter="Cohort dashboard"),
        go(f"/presentations/{PRESENTATION_AAA_2014}/dashboard/?dim=imd_band&horizon=8",
           9, "distribution, band counts, flag counts"),
        scroll(650, 11,
               "two IMD levels suppressed: n=17 and n=8, named but not described"),
        go(f"/presentations/{PRESENTATION_AAA_2014}/dashboard/?dim=disability&horizon=8",
           6, "the same breakdown by disability"),
        go(f"/presentations/{PRESENTATION_AAA_2014}/dashboard/?dim=num_prev_attempts&horizon=8",
           6, "and by previous attempts"),

        # --- Scene 8: administration -----------------------------------------
        card("s8", "Administration",
             "Upload, rebuild, retrain - all refusable, all audited",
             chapter="Administration"),
        go("/logout/", 2, "log out"),
        login("admin1", PASSWORD, 4, "sign in as the administrator"),
        go("/admin/upload/", 8,
           "seven files together; nothing stored unless every check passes"),
        go("/admin/rebuild/", 9,
           "the chunked rebuild: 21.6s, 1.55 GB peak, against 10 min and 8 GB"),
        go("/admin/retrain/", 9,
           "current models per horizon, with the metrics they earned"),

        # --- Scene 9: the audit log ------------------------------------------
        card("s9", "Append-only audit", "Every state change, exactly once",
             chapter="Audit log"),
        go("/admin/audit/", 9, "this recording is already in the log"),
        go("/admin/audit/?action=denied", 8,
           "filtered to denied attempts: the 403 from earlier"),
        go("/admin/audit/?action=flag_created", 6, "and the flag just raised"),

        card("end", "EarlyAlert",
             "310 automated tests - TC1 through TC20", hold=5,
             chapter="Close"),
    ]


# --------------------------------------------------------------------------
# Title cards
# --------------------------------------------------------------------------

CARD_HTML = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
  html,body {{ height:100%; margin:0; }}
  body {{
    background:#1f3a5f; color:#fff; display:flex; flex-direction:column;
    align-items:center; justify-content:center; text-align:center;
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
  }}
  h1 {{ font-size:64px; font-weight:600; margin:0 0 18px; letter-spacing:-.02em; }}
  p  {{ font-size:26px; margin:0; color:#cfe0f5; max-width:22ch; line-height:1.4; }}
</style>
<h1>{title}</h1>
<p>{subtitle}</p>
"""


def write_cards(steps: list[Step]) -> None:
    CARDS.mkdir(parents=True, exist_ok=True)
    for step in steps:
        if step.kind != "card":
            continue
        name, title, subtitle = json.loads(step.payload)
        (CARDS / f"{name}.html").write_text(
            CARD_HTML.format(title=title, subtitle=subtitle), encoding="utf-8"
        )


# --------------------------------------------------------------------------
# Chrome
# --------------------------------------------------------------------------

def osascript(script: str) -> tuple[int, str]:
    done = subprocess.run(["osascript", "-"], input=script, text=True,
                          capture_output=True)
    return done.returncode, (done.stdout or done.stderr).strip()


def applescript_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def chrome_go(url: str) -> tuple[int, str]:
    return osascript(
        'tell application "Google Chrome"\n'
        "  activate\n"
        f"  set URL of active tab of front window to {applescript_string(url)}\n"
        "end tell"
    )


def chrome_js(code: str) -> tuple[int, str]:
    return osascript(
        'tell application "Google Chrome"\n'
        f"  execute active tab of front window javascript {applescript_string(code)}\n"
        "end tell"
    )


# --------------------------------------------------------------------------
# Checks
# --------------------------------------------------------------------------

def check() -> int:
    problems = []

    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(BASE + "/login/", timeout=5) as response:
            ok = response.status == 200
    except (urllib.error.URLError, OSError):
        ok = False
    print(f"  server at {BASE} ............ {'ok' if ok else 'NOT RUNNING'}")
    if not ok:
        problems.append("Start it: .venv/bin/python manage.py runserver")

    code, out = osascript(
        'tell application "System Events" to return (exists process "Google Chrome")'
    )
    running = out.strip() == "true"
    print(f"  Google Chrome ............... {'ok' if running else 'NOT RUNNING'}")
    if not running:
        problems.append("Open Google Chrome, with at least one window.")

    if running:
        code, out = chrome_js("1+1")
        allowed = code == 0 and out.strip() == "2"
        print(f"  JavaScript from Apple Events  {'ok' if allowed else 'BLOCKED'}")
        if not allowed:
            problems.append(
                "In Chrome: View > Developer > Allow JavaScript from Apple Events."
            )

    print(f"  screencapture ............... "
          f"{'ok' if Path('/usr/sbin/screencapture').exists() else 'MISSING'}")

    if problems:
        print("\nFix first:")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nReady. Run without --check to record.")
    return 0


def reset() -> None:
    """Clear the flags the tour creates, so it can be run again."""
    script = f"""
from interventions.models import Flag
from cohorts.models import Student
s = Student.objects.get(pk={STUDENT_FLAGGABLE})
n = Flag.objects.filter(student=s).count()
Flag.objects.filter(student=s).delete()
print(f'cleared {{n}} flag(s) on student {{s.id_student}}')
"""
    subprocess.run(
        [str(ROOT.parent / ".venv/bin/python"), "manage.py", "shell", "-c", script],
        cwd=ROOT.parent, check=False,
    )


# --------------------------------------------------------------------------
# Run
# --------------------------------------------------------------------------

def timecode(seconds: float) -> str:
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def run(steps: list[Step], record: bool, output: Path) -> None:
    total = sum(step.hold for step in steps) + 4  # a little tail
    write_cards(steps)

    recorder = None
    if record:
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            output.unlink()
        print(f"Recording {timecode(total)} to {output}")
        print("Do not touch the keyboard or mouse until it finishes.\n")
        recorder = subprocess.Popen(
            ["/usr/sbin/screencapture", "-v", f"-V{int(total)}", "-k", "-C", "-x",
             str(output)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        time.sleep(2.5)  # let the recorder settle before the first frame

    chapters: list[tuple[float, str]] = []
    elapsed = 0.0
    started = time.monotonic()

    for index, step in enumerate(steps, start=1):
        if step.chapter:
            chapters.append((elapsed, step.chapter))

        if step.kind == "card":
            name = json.loads(step.payload)[0]
            chrome_go((CARDS / f"{name}.html").as_uri())
        elif step.kind == "go":
            chrome_go(step.payload)
        elif step.kind == "js":
            code, out = chrome_js(step.payload)
            if code != 0:
                print(f"    ! javascript refused: {out}")

        print(f"  [{timecode(elapsed)}] {index:>2}/{len(steps)}  {step.note}")
        time.sleep(step.hold)
        elapsed += step.hold

    if recorder is not None:
        remaining = total - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining + 1)
        recorder.wait(timeout=30)
        print(f"\nSaved {output}  ({timecode(total)})")

    print("\nYouTube chapters — paste into the description:")
    print("0:00 Introduction")
    for at, title in chapters:
        print(f"{timecode(at + (2.5 if record else 0))} {title}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="verify the server, Chrome and permissions, then exit")
    parser.add_argument("--reset", action="store_true",
                        help="clear the flags the tour creates, then exit")
    parser.add_argument("--dry-run", action="store_true",
                        help="walk the tour without recording")
    parser.add_argument("--out", type=Path,
                        default=ROOT.parent / "results" / "demo_tour.mov")
    arguments = parser.parse_args(argv)

    if arguments.check:
        return check()
    if arguments.reset:
        reset()
        return 0

    print("Preflight:")
    if check() != 0:
        return 1
    print()
    run(build_steps(), record=not arguments.dry_run, output=arguments.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
