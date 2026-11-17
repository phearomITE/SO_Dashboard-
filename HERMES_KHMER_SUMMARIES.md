# Khmer reason summaries with Hermes

Open Reason Summary, choose the business date/region/dealer, and click **Summarise in Khmer**. The default scope processes replies missing a current Khmer summary or retries previous failures. **Reanalyse all filtered replies** also revisits old positive/negative classifications. Each run processes at most 100 replies. Read progress, failed count and model readiness directly on the page.

Choose Khmer, English or both for display. CSV exports preserve both summaries, original source text, classification, method and AI status in UTF-8 with BOM. Original replies remain unchanged. A classification describes the explanation; it is not managerial approval.

The existing Hermes 3 model is served locally through Ollama. The prompt requests validated `summary_km` and `summary_en` fields and allows `is_reason=null` for ambiguous replies. Khmer output is checked before success is reported. Obvious outlet forms/empty replies use labelled bilingual rules without inference. Short Khmer causes are not rejected just because they have fewer than nine Unicode characters. Model failures retain prior summaries/classifications and expose a retryable error.

Validated model outputs are cached in Redis for 24 hours using a hash of the exact reply, model, endpoint and prompt version. Failed outputs are not cached. Identity details are not included in inference prompts. Input text is treated as untrusted content; instructions in a reply cannot authorize actions.

Historical reanalysis runs outside database transactions. Each write checks that the source record has not changed since reading. Records explicitly marked reviewed_by_admin are skipped. Reason-list caches are invalidated after updates. The existing bot analyzer remains compatible through the English `summary` field; after the next normal bot deployment it also stores the bilingual fields for new replies. The dashboard can summarize older or newly captured English-only rows immediately with its analysis button.

Configured through OLLAMA_URL, OLLAMA_REASON_MODEL and OLLAMA_REASON_TIMEOUT. The page does not expose credentials or add a public Ollama port. Existing Hermes service was reachable and hermes3 installed. A real Khmer connectivity example produced valid bilingual output in 8.19 seconds; model time varies with hardware and input.


Deployment verification: November 13 historical batch completed 41/41 replies, with 37 Hermes results and 4 rule results, zero failures and zero concurrent-write skips. All 41 records contained Khmer summaries. Sixteen analyzer/regression tests, three background-processing tests and the DOM test for language switching/model status/analysis submission passed.
