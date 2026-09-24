#!/usr/bin/env python3
"""Apply Arka overlay onto a cloned termux-app tree."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TERMUX = Path(sys.argv[1] if len(sys.argv) > 1 else "termux-app").resolve()


def must(p: Path) -> Path:
    if not p.exists():
        raise SystemExit(f"tidak ketemu: {p}")
    return p


def replace_once(path: Path, old: str, new: str) -> None:
    t = path.read_text(encoding="utf-8")
    if old not in t:
        raise SystemExit(f"pola tidak ketemu di {path}: {old[:80]}")
    path.write_text(t.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    strings = must(TERMUX / "app/src/main/res/values/strings.xml")
    t = strings.read_text(encoding="utf-8")
    t = t.replace('<!ENTITY TERMUX_APP_NAME "Termux">', '<!ENTITY TERMUX_APP_NAME "Arka">')
    strings.write_text(t, encoding="utf-8")

    java_dir = must(TERMUX / "app/src/main/java/com/termux/app")
    shutil.copy(ROOT / "ArkaChatActivity.java", java_dir / "ArkaChatActivity.java")

    assets = TERMUX / "app/src/main/assets"
    assets.mkdir(parents=True, exist_ok=True)
    z = ROOT / "arka.zip"
    if z.exists():
        shutil.copy(z, assets / "arka.zip")

    manifest = must(TERMUX / "app/src/main/AndroidManifest.xml")
    m = manifest.read_text(encoding="utf-8")
    if "usesCleartextTraffic" not in m:
        m = m.replace(
            "<application",
            '<application android:usesCleartextTraffic="true"',
            1,
        )
    snippet = """
        <activity
            android:name=".app.ArkaChatActivity"
            android:exported="true"
            android:label="@string/application_name"
            android:launchMode="singleTask"
            android:resizeableActivity="true"
            android:theme="@style/Theme.TermuxActivity.DayNight.NoActionBar">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
"""
    if "ArkaChatActivity" not in m:
        # TermuxActivity remains, but Arka becomes default launcher: remove LAUNCHER from TermuxActivity
        m = m.replace(
            """<intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>""",
            "<!-- launcher moved to ArkaChatActivity -->",
            1,
        )
        m = m.replace("</application>", snippet + "\n    </application>", 1)
    manifest.write_text(m, encoding="utf-8")

    act = must(TERMUX / "app/src/main/java/com/termux/app/TermuxActivity.java")
    a = act.read_text(encoding="utf-8")
    needle = "setContentView(R.layout.activity_termux);"
    inject = needle + "\n        ArkaTabs.attachTo(this, true);"
    if "ArkaTabs.attachTo" not in a:
        if needle not in a:
            raise SystemExit("setContentView tidak ketemu di TermuxActivity")
        a = a.replace(needle, inject, 1)
        act.write_text(a, encoding="utf-8")

    print("overlay Arka OK:", TERMUX)


if __name__ == "__main__":
    main()
