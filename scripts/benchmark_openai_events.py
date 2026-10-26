"""Read-only event benchmark: public CSV fixtures, no database writes."""
import argparse
import csv
import json
import os
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ai_runtime
import openai_event_verifier as verifier

ROOT = Path(__file__).resolve().parents[1]


def examples():
    seen = set()
    for filename in ("event_pair_gold_v1.csv", "event_pair_hard_negatives_v1.csv", "event_pair_story_coverage_v1.csv"):
        with (ROOT / "validation" / filename).open() as handle:
            for row in csv.DictReader(handle):
                if row["pair_id"] in seen or not row.get("human_label"):
                    continue
                seen.add(row["pair_id"])
                yield {"id": row["pair_id"], "source": filename,
                       "gold": row["human_label"] == "same_event",
                       "headline_a": row.get("headline_a_english") or row.get("headline_a"),
                       "headline_b": row.get("headline_b_english") or row.get("headline_b"),
                       "day_gap": float(row["day_gap"]) if row.get("day_gap") else None}


def run_pair(row):
    start = time.monotonic()
    result = verifier.verify(
        {"headline_english": row["headline_a"]},
        [{"evidence_id": row["id"], "headline_english": row["headline_b"], "day_gap": row["day_gap"]}],
    )
    return {**row, "prediction": result["relationship"], "result": result,
            "elapsed_seconds": round(time.monotonic() - start, 3)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", action="store_true")
    args = parser.parse_args()
    ai_runtime.require_key()
    rows = list(examples())
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run_pair, rows))
    tp = sum(r["gold"] and r["prediction"] == "same_event" for r in results)
    fp = sum(not r["gold"] and r["prediction"] == "same_event" for r in results)
    fn = sum(r["gold"] and r["prediction"] != "same_event" for r in results)
    tn = len(results) - tp - fp - fn
    invalid = sum(bool(r["result"].get("error")) for r in results)
    passed = fp == 0 and tp >= max(1, (tp + fn) / 2) and invalid == 0
    summary = {"pairs": len(results), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
               "precision": tp / (tp + fp) if tp + fp else 0,
               "recall": tp / (tp + fn) if tp + fn else 0,
               "unclear": sum(r["prediction"] == "unclear" for r in results),
               "invalid": invalid, "gate_passed": passed,
               "wall_seconds": round(time.monotonic() - start, 3),
               "mean_request_seconds": round(statistics.mean(r["elapsed_seconds"] for r in results), 3),
               "cost_usd": round(sum(r["result"].get("audit", {}).get("cost_usd", 0) for r in results), 6)}
    report = {"model": verifier.identity(), "summary": summary,
              "warning": "Small development sample, not an independent estimate of production accuracy.",
              "pairs": results}
    path = ROOT / ".ai-usage/event-benchmark.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(summary, indent=2), flush=True)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as handle:
            handle.write("## Event verifier benchmark\n\n```json\n" + json.dumps(summary, indent=2) + "\n```\n")
    if args.gate and not passed:
        raise SystemExit("Benchmark gate failed: require zero false merges, >=50% positive recall and no invalid outputs")


if __name__ == "__main__":
    main()
