# AI model schedule

The Observatory uses `config/ai-models.json` and `scripts/ai_runtime.py` for API model selection:

| New job starts | Provider / model |
| --- | --- |
| Before 10 December 2026, 00:00 Europe/Amsterdam | OpenAI `gpt-5-nano` |
| From 10 December 2026, 00:00 Europe/Amsterdam | OpenAI `gpt-6-luna` |

The model is fixed at job startup. A job crossing midnight finishes with its original model; new jobs use the new one. The transition is implemented in code and does not require a December commit. A process override `AIEO_AI_MODEL` is supported, but a stale GPT-5 nano override cannot bypass the December cutoff. The configured successor is the cheapest non-deprecated suitable model verified on 26 October; a scheduled December review will recheck prices and availability.

Add `OPENAI_API_KEY` as a **repository Actions secret** in this GitHub repository. The key must have permission to call the configured OpenAI models and usable API credit. Existing Supabase secrets remain required. No key belongs in a public file or frontend environment variable.

The default estimated/reserved budget is **US$0.50 per inference job**, with at most 350 API attempts. Each request reserves conservative input and maximum output costs before it is sent. Usage replaces that reservation after a complete API response. Uncertain transport failures keep the reservation. Rates are deliberately conservative; API billing is authoritative. The limit can be changed with the repository variable `AIEO_AI_MAX_JOB_USD`.

This is not a monthly account limit. A three-pass Observatory classification workflow can reserve up to $1.50 across its three jobs; the weekly pipeline contains more than one classification stage. A Brief job shares its $0.50 limit between news and research. Re-running jobs starts new budgets. Use a dedicated API project and monitor its account usage as well.

The client uses strict JSON, low reasoning effort for Nano and no reasoning for the scheduled GPT-6 Luna successor, bounded requests, no paid tool calls, and `store: false`. Ledgers contain hashes, model IDs, timing and token counts, not article bodies or keys. They appear in workflow artifacts and the run summary. Quota/authentication/schema errors stop useful inference rather than silently switching provider. Existing validators still reject unsupported quotations, invalid axes, unfinished answers and inconsistent source bindings.

For existing interrupted classifications, only the matching model/revision may resume. Older completed runs and human decisions remain recorded. Brief draft fingerprints also include the model revision, so a model change generates a new version instead of relabelling old text.

The Brief imports only `/data/brief/current.json`, the Observatory's complete-content export. Its release hashes, source eligibility and evidence fingerprints are validated together. Missing companion sources are not passed to the writer. “No direction stated” remains a valid result from complete evidence. A paid model does not turn missing evidence into complete evidence.

The API path avoids downloading and running a local model. It also supplies ordinary complete articles directly to the writer, avoiding unnecessary intermediate notes. This should reduce local CPU bottlenecks, but speed and classification quality must be measured on the first live run. No paid API requests were made during offline verification.

For an explicit rollback, set the repository variable `AIEO_AI_PROVIDER=local`; this restores the existing local generation path for the classification/writing jobs. Event resolution now requires OpenAI and does not restore the unavailable ModernBERT checkpoint. Remove the variable or set it to `openai` to restore the scheduled API path.

After deployment, compare a small sample of Nano outputs against the full sources: include benefit, harm, mixed and “no direction stated” records across the collection languages. Check quotation support, population scope and the final paragraph. Do not improve headline percentages by accepting unsupported classifications.

## Event resolver replacement (26 October 2026)

Event resolution keeps multilingual MiniLM candidate retrieval and replaces both
ModernBERT pair scoring and the local Qwen verifier with one bounded OpenAI request
per candidate event, plus an independent source-only check for proposed matches.
The first request classifies up to three source headlines. Only an
explicit same-event result with sufficient retrieval support and no competing
candidate may merge automatically. Unclear or invalid outputs remain separate for
review. API/auth/budget failures stop the run; they do not generate event labels.

The old ModernBERT probabilities and Qwen fields are null for new decisions.
Actual labels, reasons and requested/returned model provenance are stored in the
existing evidence JSON. Model-labelled confidence is not a calibrated probability.
A local result cache is keyed by input, model and prompt version. The dedicated
benchmark uses public CSV labels and has no database credentials or writes.

Standard rates verified 26 October 2026: Nano $0.05 input / $0.40 output per million
tokens; GPT-6 Luna $0.10 input / $0.50 output. The ledger reserves $0.125/M Luna input
tokens conservatively for possible cache-write premiums. Nano reasoning tokens are
included in billable output. The event job caps output at 2,048 tokens per request.
The successor changes at midnight Amsterdam time (9 December 23:00 UTC).

Sources: [GPT-5 nano](https://developers.openai.com/api/docs/models/gpt-5-nano),
[GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna),
[pricing](https://developers.openai.com/api/docs/pricing), and
[Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Historical reconciliation and human labels

Historical reconciliation uses the same scheduled OpenAI client. Accepted
versioned human decisions still control historical merges and follow-on links;
model proposals cannot change those identities without human acceptance. The
legacy pair scores and Qwen fields are null, and actual model provenance is
recorded separately. Both event jobs retain separate $0.50 default spending caps.

The existing human pair datasets contain 75 unique pairs: 22 calibration pairs,
45 active-learning negatives, and 8 story-coverage pairs (6 positive pairs total).
The old verifier benchmark reads only the 22 calibration pairs. The replacement
benchmark evaluates all 75, preserving the labels and reporting disagreements.
These are development/evaluation labels, not model training or in-context examples.
Nine assignment reviews (3 merges, 6 keep-separate) were applied in production on
13 August 2026. The separate 23-entry historical decision file is attributed to
an artifact audit, so it is not treated as owner-labelled training data.

Older manual-only sample-generation and legacy benchmark workflows still depend
on the unavailable checkpoint. They are not part of the weekly production path;
use the new API benchmark to evaluate this replacement.

The first Nano benchmark (26 October) returned 4 true matches, 1 false match and
2 missed matches among the 75 development pairs, with 24 unclear and no invalid
outputs. Its zero-false-match gate failed. The second-check revision retains the
same labels and is evaluated on the same development set; improvement on this set
is not independent validation.
