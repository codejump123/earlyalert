#!/usr/bin/env python3
"""Self-driving screen tour of EarlyAlert, recorded to a .mov.

Drives Google Chrome through the browser scenes of the demo script on a timer
while macOS records the screen, so the on-screen actions happen on cue and you
only have to narrate over the result.

    .venv/bin/python tools/demo_tour.py --check     # verify everything first
    .venv/bin/python tools/demo_tour.py --reset     # clear the demo flags
    .venv/bin/python tools/demo_tour.py --dry-run   # walk it without recording
    .venv/bin/python tools/demo_tour.py             # record

Covers all eleven scenes of the demo script: the problem cards, the test suite
and the experiment in Terminal, the seven browser scenes, and the result
figures. About ten minutes. Only the narration is left to you.

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


def local(path: Path, hold: float, note: str, chapter: str = "") -> Step:
    """Open a file from disk in Chrome: a figure, or the results page."""
    return Step(hold, "go", path.resolve().as_uri(), note=note, chapter=chapter)


def term(command: str, hold: float, note: str, chapter: str = "") -> Step:
    """Run a command in Terminal, in front, so the recording catches it."""
    return Step(hold, "term", command, note=note, chapter=chapter)


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
    repo = ROOT.parent
    results = repo / "results"

    return [
        # --- Scene 1: the problem --------------------------------------------
        card("title", "EarlyAlert",
             "Predicting student withdrawal, early enough to act",
             hold=9, chapter="The problem"),
        card("p1", "10,156 of 32,593",
             "registrations in OULAD end in withdrawal - 31.2%", hold=11),
        card("p2", "37.2% against 25.9%",
             "most deprived decile against least deprived", hold=12),
        card("p3", "Week 4 · Week 8 · Week 12",
             "A separate model at each, because predicting early has a cost",
             hold=11),

        # --- Scene 2: what was built ------------------------------------------
        card("s2", "What was built",
             "Six Django apps, and one package with no Django in it",
             hold=7, chapter="What was built"),
        term(f"cd {repo} && clear && ls -1 -d */ | grep -v -E 'venv|results|uploads|artifacts|jobs|tools'",
             13, "the apps, and pipeline"),
        term(f"cd {repo} && clear && ls -1 pipeline/*.py && echo && "
             f"echo 'no Django imports anywhere in pipeline:' && "
             f"(grep -rl 'from django\\|import django' pipeline/ || echo '  none')",
             15, "pipeline imports nothing from Django"),
        term(f"cd {repo} && clear && .venv/bin/python -m pytest -q 2>&1 | tail -6",
             72, "310 tests, TC1 through TC20, all automated"),

        # --- Scene 3: roles and authorization ---------------------------------
        card("s3", "Three roles, enforced",
             "Authorization in one service layer, not per view",
             hold=7, chapter="Roles and authorization"),
        go("/logout/", 3, "log out of any existing session"),
        go("/presentations/", 6, "a protected page with no session bounces to login"),
        login("advisor1", "wrong-password", 8,
              "wrong password: 'Username or password not recognized.'"),
        login("nosuchuser", "wrong-password", 9,
              "unknown username: the identical message, so the form reveals nothing"),
        login("advisor1", PASSWORD, 6, "correct credentials"),
        go("/presentations/", 9,
           "the selector lists only assigned presentations: 3 of the 22 loaded"),
        go(f"/presentations/{PRESENTATION_UNASSIGNED}/ranking/", 14,
           "URL tampering to an unassigned presentation -> 403, nothing disclosed"),

        # --- Scene 4: the ranking ---------------------------------------------
        card("s4", "The ranking",
             "972 scored students, read from stored scores",
             hold=6, chapter="The ranking"),
        go(f"/presentations/{PRESENTATION_DDD}/ranking/?horizon=8", 14,
           "week 8: probability, risk band, leading reason, flag status"),
        scroll(700, 9, "scroll the ranking"),
        scroll(0, 4, "back to the top"),
        go(f"/presentations/{PRESENTATION_DDD}/ranking/?horizon=4", 9,
           "week 4 reads a different model's stored scores - nothing is computed now"),
        go(f"/presentations/{PRESENTATION_DDD}/ranking/?horizon=12", 8, "week 12"),
        go(f"/presentations/{PRESENTATION_DDD}/ranking/?horizon=8&page=2", 8,
           "page 2 - bands are cohort positions, so they continue across pages"),
        go(f"/presentations/{PRESENTATION_DDD}/ranking/export.csv?horizon=8", 8,
           "the export is the whole ranking, and is itself audited"),

        # --- Scene 5: one student ---------------------------------------------
        card("s5", "One student, explained",
             "The score, and the behaviour behind it",
             hold=6, chapter="Student detail"),
        go(f"/students/{STUDENT_FLAGGABLE}/?horizon=8", 14,
           "demographics, probability 0.632, band High, three reasons"),
        scroll(750, 11, "the weekly engagement history the score was built from"),
        scroll(0, 3, "back to the top"),
        go(f"/students/{STUDENT_NO_DATA}/", 13,
           "a registration with no clickstream: it says so, and shows no probability"),

        # --- Scene 6: the intervention workflow --------------------------------
        card("s6", "The intervention workflow",
             "Flag, contact, outcome - and what each closure means afterwards",
             hold=7, chapter="Intervention workflow"),
        go(f"/students/{STUDENT_FLAGGABLE}/?horizon=8", 5, "back to the student"),
        scroll(2400, 6, "down to the flag control"),
        js("""
           var f = document.querySelector('form[action*="/flag/"]');
           f.querySelector('textarea[name=reason]').value =
             'No VLE activity for three weeks and two assessments unsubmitted.';
           setTimeout(function(){ f.submit(); }, 1600);
           """, 11, "raise a flag with a reason"),
        js("""
           var f = document.querySelector('form textarea[name=note]').form;
           f.querySelector('textarea[name=note]').value =
             'Left a voicemail on the number on file.';
           setTimeout(function(){ f.submit(); }, 1600);
           """, 11, "add a note - notes append, nothing is overwritten"),
        js(f"""
           var f = document.querySelector('select[name=intervention_type]').form;
           f.querySelector('select[name=intervention_type]').value = 'phone';
           f.querySelector('input[name=date]').value = {json.dumps(tomorrow)};
           setTimeout(function(){{ f.submit(); }}, 1800);
           """, 13, "an intervention dated tomorrow: refused, with the reason"),
        js(f"""
           var f = document.querySelector('select[name=intervention_type]').form;
           f.querySelector('select[name=intervention_type]').value = 'phone';
           f.querySelector('input[name=date]').value = {json.dumps(today)};
           f.querySelector('textarea[name=notes]').value =
             'Spoke briefly. Agreed to submit TMA02 by Friday.';
           setTimeout(function(){{ f.submit(); }}, 1800);
           """, 11, "record the intervention properly"),
        js("""
           var f = document.querySelector('form[action*="/outcome/"]');
           f.querySelector('select[name=outcome]').value = 're_engaged';
           setTimeout(function(){ f.submit(); }, 1800);
           """, 11, "record the outcome: the flag closes as resolved"),
        go(f"/students/{STUDENT_FLAGGABLE}/?horizon=8", 5, "back to the student"),
        scroll(2400, 11,
               "closed as resolved: the control is gone for the rest of the presentation"),
        go(f"/students/{STUDENT_WITHDRAWN}/?horizon=8", 5, "a student who withdrew"),
        scroll(2400, 11,
               "already unregistered: control disabled, and the day is shown"),

        # --- Scene 7: the dashboard --------------------------------------------
        card("s7", "Small cells are suppressed",
             "A group of fewer than 20 is named, and not described",
             hold=7, chapter="Cohort dashboard"),
        go(f"/presentations/{PRESENTATION_AAA_2014}/dashboard/?dim=imd_band&horizon=8",
           12, "distribution, risk band counts, open and closed flags"),
        scroll(650, 16,
               "two IMD levels suppressed at n=17 and n=8 - named, not described"),
        go(f"/presentations/{PRESENTATION_AAA_2014}/dashboard/?dim=disability&horizon=8",
           8, "the same breakdown by disability"),
        go(f"/presentations/{PRESENTATION_AAA_2014}/dashboard/?dim=num_prev_attempts&horizon=8",
           8, "and by previous attempts"),

        # --- Scene 8: administration -------------------------------------------
        card("s8", "Administration",
             "Upload, rebuild, retrain - all refusable, all audited",
             hold=7, chapter="Administration"),
        go("/logout/", 3, "log out"),
        login("admin1", PASSWORD, 6, "sign in as the administrator"),
        go("/admin/upload/", 12,
           "seven files together; nothing is stored unless every check passes"),
        go("/admin/rebuild/", 14,
           "the chunked rebuild: 21.6s and 1.55 GB, against 10 min and 8 GB"),
        go("/admin/retrain/", 13,
           "the current model at each horizon, with the metrics it earned"),

        # --- Scene 9: the audit log --------------------------------------------
        card("s9", "Append-only audit", "Every state change, exactly once",
             hold=6, chapter="Audit log"),
        go("/admin/audit/", 13, "this recording is already in the log"),
        go("/admin/audit/?action=denied", 10,
           "filtered to denied attempts: the 403 from earlier"),
        go("/admin/audit/?action=flag_created", 8, "and the flag just raised"),

        # --- Scene 10: the experiment -------------------------------------------
        card("s10", "The experiment",
             "4 feature sets x 3 horizons x 3 classifiers",
             hold=7, chapter="The experiment"),
        term(f"cd {repo} && clear && .venv/bin/python -m pipeline.experiment "
             f"--data /Users/isit/oulad --out results 2>&1 | tail -8",
             46, "36 cells in about 32 seconds, with no database and no server"),
        local(results / "fig_aucpr_vs_horizon.png", 24,
              "left panel falls, right panel rises - the floor moves with the horizon"),
        local(results / "results.html", 26,
              "lift by feature set: engagement is the weakest of the four"),
        local(results / "fig_calibration_w8.png", 18,
              "calibration: what Brier score catches that AUC does not"),
        local(results / "fnr.html", 30,
              "false negative rate by IMD band at a 10% advisory capacity"),
        local(results / "fig_subgroup_auc_imd_w8.png", 16,
              "and AUC by IMD band, for comparison"),

        # --- Scene 11: close -----------------------------------------------------
        card("end", "EarlyAlert",
             "310 automated tests - TC1 through TC20", hold=9, chapter="Close"),
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


def terminal_run(command: str) -> tuple[int, str]:
    """Bring Terminal forward and run one command in the same window."""
    return osascript(
        'tell application "Terminal"\n'
        "  activate\n"
        "  if (count of windows) is 0 then\n"
        f"    do script {applescript_string(command)}\n"
        "  else\n"
        f"    do script {applescript_string(command)} in front window\n"
        "  end if\n"
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
    write_result_pages()

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
        elif step.kind == "term":
            terminal_run(step.payload)
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
    parser.add_argument("--pace", type=float, default=1.0,
                        help="scale how long each screen is held (terminal waits "
                             "are left alone); 0.75 gives about ten minutes")
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
    steps = build_steps()
    if arguments.pace != 1.0:
        # Terminal steps wait on a real command; scaling those would cut the
        # test suite off mid-run. Only the reading time is adjustable.
        for step in steps:
            if step.kind != "term":
                step.hold = round(step.hold * arguments.pace, 1)
    run(steps, record=not arguments.dry_run, output=arguments.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())


# --------------------------------------------------------------------------
# Result pages for scene 10
# --------------------------------------------------------------------------

PAGE_HTML = """<!doctype html>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ margin:0; padding:56px 64px; background:#0f1b2b; color:#eaf1fa;
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
  h1 {{ font-size:38px; margin:0 0 6px; font-weight:600; letter-spacing:-.02em; }}
  p.sub {{ font-size:19px; color:#9db9d8; margin:0 0 30px; }}
  table {{ border-collapse:collapse; font-size:21px; font-variant-numeric:tabular-nums; }}
  th {{ text-align:left; font-size:14px; letter-spacing:.08em; text-transform:uppercase;
        color:#7e9bbd; font-weight:600; padding:0 26px 10px 0;
        border-bottom:1px solid #2b4363; }}
  td {{ padding:9px 26px 9px 0; border-bottom:1px solid #1d3049; }}
  td.k {{ color:#9db9d8; }}
  .hi {{ color:#8ed6a8; font-weight:600; }}
  .lo {{ color:#e5927e; font-weight:600; }}
  .note {{ margin-top:28px; font-size:17px; color:#9db9d8; max-width:70ch; line-height:1.5; }}
</style>
<h1>{title}</h1>
<p class="sub">{subtitle}</p>
{table}
<p class="note">{note}</p>
"""


def write_result_pages() -> None:
    """Build the two tables scene 10 shows, from the real results."""
    import csv as csv_module

    results = ROOT.parent / "results"
    results.mkdir(exist_ok=True)

    # Lift by feature set and horizon, straight out of grid.csv.
    grid = results / "grid.csv"
    if grid.exists():
        best: dict[tuple[str, int], float] = {}
        with grid.open(newline="", encoding="utf-8") as handle:
            for row in csv_module.DictReader(handle):
                key = (row["feature_set"], int(row["horizon_week"]))
                lift = float(row["auc_pr_lift"])
                best[key] = max(best.get(key, 0.0), lift)
        horizons = sorted({h for _, h in best})
        names = {"D": "D  demographic", "A": "A  assessment",
                 "E": "E  engagement", "All": "All"}
        rows = ""
        for code in ("D", "A", "E", "All"):
            cells = "".join(
                f"<td>&times;{best[(code, h)]:.2f}</td>" for h in horizons
                if (code, h) in best
            )
            rows += f"<tr><td class='k'>{names[code]}</td>{cells}</tr>"
        head = "".join(f"<th>week {h}</th>" for h in horizons)
        table = f"<table><tr><th>feature set</th>{head}</tr>{rows}</table>"
        (results / "results.html").write_text(
            PAGE_HTML.format(
                title="Lift over the per-horizon baseline",
                subtitle="Best classifier in each cell. Larger is better.",
                table=table,
                note="Engagement is the weakest of the four sets at every horizon, "
                     "below demographics throughout. What predicts withdrawal is "
                     "assessment behaviour, not clickstream volume.",
            ),
            encoding="utf-8",
        )

    # False negative rate by IMD band, from the stored subgroup metrics.
    script = (
        "import json;"
        "from scoring.models import ModelVersion;"
        "m=ModelVersion.objects.filter(horizon_week=8,is_current=True).first();"
        "print(json.dumps(m.subgroup_metrics.get('imd_band',{}) if m else {}))"
    )
    try:
        done = subprocess.run(
            [str(ROOT.parent / ".venv/bin/python"), "manage.py", "shell", "-c", script],
            cwd=ROOT.parent, capture_output=True, text=True, timeout=90,
        )
        report = json.loads(done.stdout.strip().splitlines()[-1])
    except Exception:
        report = {}

    levels = report.get("levels", {})
    if levels:
        rows = ""
        for level in sorted(levels):
            entry = levels[level]
            if entry.get("suppressed"):
                rows += (f"<tr><td class='k'>{level}</td><td>{entry['n']}</td>"
                         f"<td colspan='3' class='k'>suppressed (n &lt; 20)</td></tr>")
                continue
            fnr = entry["false_negative_rate"]
            cls = "hi" if fnr < 0.73 else ("lo" if fnr > 0.79 else "")
            rows += (
                f"<tr><td class='k'>{level}</td><td>{entry['n']}</td>"
                f"<td>{entry['positives']}</td>"
                f"<td class='{cls}'>{fnr:.3f}</td>"
                f"<td>{entry['auc_roc']:.3f}</td></tr>"
            )
        table = (
            "<table><tr><th>IMD band</th><th>students</th><th>withdrew</th>"
            "<th>missed</th><th>AUC-ROC</th></tr>" + rows + "</table>"
        )
        (results / "fnr.html").write_text(
            PAGE_HTML.format(
                title="Who actually gets missed",
                subtitle="False negative rate at a 10% advisory capacity, week 8. "
                         "Lower is better.",
                table=table,
                note="At this capacity the system misses roughly three quarters of "
                     "all withdrawers. And the most deprived band is missed least, "
                     "not most - the opposite of what the AUC column suggests.",
            ),
            encoding="utf-8",
        )
