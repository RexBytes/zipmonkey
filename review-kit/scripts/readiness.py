#!/usr/bin/env python3
"""Compute zipmonkey's Release-Readiness Score (RRS) and convergence.

See RELEASE_READINESS.md for the rubric. Reads release_readiness.json for the
severity weights and the review-panel history; runs the hard gates (pytest /
ruff / mypy) and coverage unless --no-gates is given.

Usage:
    python scripts/readiness.py
    python scripts/readiness.py --no-gates
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str]) -> tuple[int, str]:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def run_gates() -> tuple[dict[str, bool], float]:
    """Run pytest (+coverage), ruff, mypy. Return gate booleans and coverage."""
    cov_json = ROOT / ".readiness_cov.json"
    rc_pytest, _ = _run(
        [
            sys.executable, "-m", "pytest", "-q",
            "--cov=zipmonkey", f"--cov-report=json:{cov_json}",
        ]
    )
    coverage = 0.0
    if cov_json.exists():
        try:
            coverage = json.loads(cov_json.read_text())["totals"][
                "percent_covered"
            ] / 100.0
        except (KeyError, ValueError):
            coverage = 0.0
        cov_json.unlink(missing_ok=True)
    rc_ruff, _ = _run(["ruff", "check", "src", "tests"])
    rc_mypy, _ = _run(["mypy"])
    gates = {
        "tests_pass": rc_pytest == 0,
        "ruff_clean": rc_ruff == 0,
        "mypy_clean": rc_mypy == 0,
    }
    return gates, coverage


def weighted(findings: dict[str, float], weights: dict[str, float]) -> float:
    return sum(weights.get(sev, 0) * n for sev, n in findings.items())


def convergence(cfg: dict) -> dict:
    weights = cfg["severity_weights"]
    tau = cfg["tau"]
    panels = cfg["panels"]
    if not panels:
        return {"rate": 1.0, "decline": 0.0, "confidence": 0.0,
                "score": 0.0, "streak": 0, "per_panel": []}

    # Full diversity = all *available* distinct models participated. Configure
    # available_models (e.g. when a model is unavailable, like Fable here);
    # otherwise infer it from the distinct models seen across panels. A model
    # name may appear in variants (e.g. "sonnet-philosophy" -> "sonnet").
    def _base(m: str) -> str:
        return m.split("-", 1)[0]

    available = {_base(m) for m in cfg.get("available_models", [])}
    if not available:
        available = {_base(m) for p in panels for m in p["models"]}
    per_panel = []
    for p in panels:
        w = weighted(p["findings"], weights)
        distinct = {_base(m) for m in p["models"]} & available
        diversity = len(distinct) / len(available) if available else 0.0
        per_panel.append({"id": p["id"], "weighted": w, "diversity": diversity})

    w_first = per_panel[0]["weighted"] or 1.0
    w_last = per_panel[-1]["weighted"]
    rate = w_last / w_first
    decline = 1.0 - min(1.0, rate)

    # Clean streak: trailing panels with weighted yield < tau.
    streak = 0
    streak_diversity = 1.0
    for p in reversed(per_panel):
        if p["weighted"] < tau:
            streak += 1
            streak_diversity = min(streak_diversity, p["diversity"])
        else:
            break
    confidence = 1.0 - math.exp(-streak * streak_diversity) if streak else 0.0
    score = 0.5 * decline + 0.5 * confidence
    return {
        "rate": rate, "decline": decline, "confidence": confidence,
        "score": score, "streak": streak, "per_panel": per_panel,
    }


def file_has(path: str, needle: str) -> bool:
    p = ROOT / path
    return p.exists() and needle in p.read_text(errors="ignore")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-gates", action="store_true",
                    help="skip running pytest/ruff/mypy (assume green)")
    args = ap.parse_args()

    cfg = json.loads((ROOT / "release_readiness.json").read_text())

    if args.no_gates:
        gates = {"tests_pass": True, "ruff_clean": True, "mypy_clean": True}
        coverage = cfg["coverage_target"]
    else:
        gates, coverage = run_gates()
    gates["no_open_defects"] = cfg.get("known_open_defects", 0) == 0

    conv = convergence(cfg)

    # Project-specific signals are configured in release_readiness.json so this
    # script stays byte-identical across projects; sane defaults keep it working
    # if the config omits them.
    sig = cfg.get("signals", {})
    prop = sig.get(
        "property_tests", {"file": "tests/test_properties.py", "needle": "hypothesis"}
    )
    sec = sig.get(
        "security",
        {"file": "tests/test_safety.py", "needles": ["traversal", "escape"]},
    )
    docs = sig.get(
        "docs", ["LIMITATIONS.md", "SKILL.md", "CONTRIBUTING.md", "README.md"]
    )

    # Component scores (0..1).
    comp = {
        "coverage": (15, min(1.0, coverage / cfg["coverage_target"])),
        "property_tests": (
            10, 1.0 if file_has(prop["file"], prop["needle"]) else 0.0
        ),
        "contract_coverage": (20, cfg["contract_coverage_estimate"]),
        "convergence": (25, conv["score"]),
        "static_rigor": (15, 1.0 if gates["ruff_clean"]
                         and gates["mypy_clean"] else 0.0),
        "docs": (10, sum((ROOT / d).exists() for d in docs) / len(docs)),
        "security": (
            5,
            1.0 if any(file_has(sec["file"], n) for n in sec["needles"]) else 0.0,
        ),
    }
    raw = sum(w * s for w, s in comp.values())

    gates_pass = all(gates.values())
    rrs = raw if gates_pass else min(raw, 40.0)

    streak_ok = conv["streak"] >= 2 and any(
        p["diversity"] >= 1.0 for p in conv["per_panel"][-2:]
    )
    releasable = gates_pass and rrs >= 90 and streak_ok

    # --- report ---
    print("=" * 56)
    print(" zipmonkey Release Readiness")
    print("=" * 56)
    print("Gates:")
    for k, v in gates.items():
        print(f"  {'PASS' if v else 'FAIL'}  {k}")
    print(f"\nCoverage: {coverage * 100:.1f}%")
    print("\nRRS components (weight x score = points):")
    for name, (w, s) in comp.items():
        print(f"  {name:<18} {w:>3} x {s:0.2f} = {w * s:5.1f}")
    print(f"\n  Release-Readiness Score (RRS): {rrs:.1f} / 100")
    print("\nConvergence:")
    for p in conv["per_panel"]:
        print(f"  panel {p['id']}: weighted={p['weighted']:.1f} "
              f"diversity={p['diversity']:.2f}")
    print(f"  convergence rate (last/first): {conv['rate']:.3f}")
    print(f"  clean streak: {conv['streak']}  "
          f"confidence: {conv['confidence']:.2f}")
    print("\n" + "-" * 56)
    verdict = "RELEASABLE" if releasable else "NOT RELEASABLE"
    print(f" Verdict: {verdict}")
    if not releasable:
        reasons = []
        if not gates_pass:
            reasons.append("a hard gate failed")
        if rrs < 90:
            reasons.append(f"RRS {rrs:.1f} < 90")
        if not streak_ok:
            reasons.append("need >=2 consecutive full-diversity clean panels")
        print(" Blocked by: " + "; ".join(reasons))
    print("-" * 56)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
