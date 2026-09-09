# AI model schedule

Both repositories use the same `config/ai-models.json` and `scripts/ai_runtime.py` contract:

| New job starts | Provider / model |
| --- | --- |
| Before 10 December 2026, 00:00 UTC | OpenAI `gpt-5-nano` |
| From 10 December 2026, 00:00 UTC | OpenAI `gpt-5.6-luna` |

The model is fixed at job startup. A job crossing midnight finishes with its original model; new jobs use the new one. The transition is implemented in code and does not require a December commit. A direct process override `AIEO_AI_MODEL` takes precedence, so do not pin it when running the automatic schedule.

Add `OPENAI_API_KEY` as a **repository Actions secret** in both GitHub repositories before pushing/merging this update. The key must have permission to call the configured OpenAI models and usable API credit. Existing Supabase secrets remain required. No key belongs in a public file or frontend environment variable.

The default estimated/reserved budget is **US$0.50 per inference job**, with at most 350 API attempts. Each request reserves conservative input and maximum output costs before it is sent. Usage replaces that reservation after a complete API response. Uncertain transport failures keep the reservation. Rates are deliberately conservative; API billing is authoritative. The limit can be changed with the repository variable `AIEO_AI_MAX_JOB_USD`.

This is not a monthly account limit. A three-pass Observatory classification workflow can reserve up to $1.50 across its three jobs; the weekly pipeline contains more than one classification stage. A Brief job shares its $0.50 limit between news and research. Re-running jobs starts new budgets. Use a dedicated API project and monitor its account usage as well.

The client uses strict JSON, low reasoning effort, bounded requests, no paid tool calls, and `store: false`. Ledgers contain hashes, model IDs, timing and token counts, not article bodies or keys. They appear in workflow artifacts and the run summary. Quota/authentication/schema errors stop useful inference rather than silently switching provider. Existing validators still reject unsupported quotations, invalid axes, unfinished answers and inconsistent source bindings.

For existing interrupted classifications, only the matching model/revision may resume. Older completed runs and human decisions remain recorded. Brief draft fingerprints also include the model revision, so a model change generates a new version instead of relabelling old text.

The Brief imports only `/data/brief/current.json`, the Observatory's complete-content export. Its release hashes, source eligibility and evidence fingerprints are validated together. Missing companion sources are not passed to the writer. “No direction stated” remains a valid result from complete evidence. A paid model does not turn missing evidence into complete evidence.

The API path avoids downloading and running a local model. It also supplies ordinary complete articles directly to the writer, avoiding unnecessary intermediate notes. This should reduce local CPU bottlenecks, but speed and classification quality must be measured on the first live run. No paid API requests were made during offline verification.

For an explicit rollback, set the repository variable `AIEO_AI_PROVIDER=local`; this restores the existing local generation path. Remove the variable or set it to `openai` to restore the scheduled API path.

After deployment, compare a small sample of Nano outputs against the full sources: include benefit, harm, mixed and “no direction stated” records across the collection languages. Check quotation support, population scope and the final paragraph. Do not improve headline percentages by accepting unsupported classifications.

Official documentation checked 8 September 2026: [GPT-5 nano](https://developers.openai.com/api/docs/models/gpt-5-nano), [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs). Luna has a higher token price than Nano; the transition is not a cost reduction.
