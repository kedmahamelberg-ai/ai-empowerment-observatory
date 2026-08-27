#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, re
from supabase import create_client

def word_count(text):
    return len(re.findall(r"\S+", text or ""))

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=20)
    args = p.parse_args()

    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    rows = (client.table("brief_article_content_snapshots")
            .select("article_id,source_domain,word_count,extraction_quality,retrieval_method,body_text,retrieved_at")
            .eq("is_current", True).order("retrieved_at", desc=True)
            .limit(args.limit).execute().data or [])

    issues = []
    preview = []
    for r in rows:
        text = r.get("body_text") or ""
        n = word_count(text)
        if n != int(r.get("word_count") or 0):
            issues.append({"article_id":r["article_id"],"issue":"word_count_mismatch","stored":r.get("word_count"),"actual":n})
        if n < 80:
            issues.append({"article_id":r["article_id"],"issue":"too_short","actual":n})
        preview.append({
            "article_id":r["article_id"],
            "domain":r.get("source_domain"),
            "words":n,
            "quality":r.get("extraction_quality"),
            "method":r.get("retrieval_method"),
            "preview":re.sub(r"\s+"," ",text[:350]).strip(),
        })

    outcomes = (client.table("brief_article_fetch_attempts")
                .select("outcome").execute().data or [])
    counts = {}
    for row in outcomes:
        value = row.get("outcome") or "unknown"
        counts[value] = counts.get(value,0)+1

    result = {"current_snapshot_sample":preview,"issues":issues,"fetch_outcomes_all_time":counts}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if issues:
        raise SystemExit("Content quality audit found structural issues.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
