# GrokBot migration plan: GPT-6 Astra

Prepared September 11, 2026. Status: provisional; source code is not present.

The inspected workspace contains only `.git`, has no tracked files, and has no commits or branches containing an application. The current provider, model, language, SDK, prompts, deployment, and tests are unknown. The project name does not establish that it uses xAI. This plan concerns the application's model integration, not the model running the Codex task.

## 1. Establish the application baseline

Locate the actual repository or restore its source into an authorized Codex checkout. Record the starting revision and inventory model configuration, provider URLs, authentication, API call sites, prompts, tool execution, streaming, conversation storage, output validation, retries, and deployment settings.

Create a file-by-file change list from that inventory. Preserve specialized models and intentional fallback routes. If no existing application exists, reclassify the work as a new integration rather than claiming a migration.

Exit condition: every active model call is mapped to its workload and output contract, and the current behavior can be reproduced.

## 2. Define and validate the target configuration

Use `gpt-6-astra`. Retain supported effective reasoning effort; map `none` or `minimal` to `low`. Tool calling requires Responses; text-only Chat Completions can remain. Remove `temperature`, `top_p`, and `top_logprobs`; also remove Chat Completions `logprobs` or Responses `message.output_text.logprobs` includes. If present, migrate old `prompt_cache_retention` to `prompt_cache_options.ttl: "30m"`. EU data residency requires Standard processing. These are official compatibility requirements, conditional on what the source inventory finds. [OpenAI migration guidance](https://developers.openai.com/api/docs/guides/latest-model)

Before implementation or live API testing, follow the OpenAI Platform API Key skill and verify project access to the target model. Check the existing SDK's relevant request support before deciding whether an upgrade is necessary. No new dependency is assumed by this plan.

Exit condition: target request shape, credentials mechanism, model access, and any provider or endpoint changes are documented.

## 3. Implement the smallest compatible integration

Make the target selectable through the existing configuration mechanism and retain the baseline deployment for rollback. If the current provider is external, update authentication, endpoint, and response handling together; a model-name replacement alone is insufficient.

Where Responses migration is required, adapt message history, tool schemas, call/result IDs, streamed events, final-text extraction, and usage accounting. Preserve the application's externally visible output schema. Validate tool arguments before execution, handle multiple calls, and ensure retries cannot repeat side effects. Retain existing session behavior through an explicit history mapping.

Keep the first prompt baseline stable. Change instructions only when evaluation exposes a concrete failure. Defer optional async tools, WebSockets, and new orchestration until the basic migration passes.

Exit condition: representative application flows work through the selected target and through the rollback path.

## 4. Evaluate behavior, reliability, latency, and cost

Prepare a representative baseline set covering common requests, difficult requests, multi-turn conversations, structured output, and tools or images where used. Add contract checks for request parameters, parsing, cancellation, timeout, rate limits, incomplete responses, and tool errors. Replay side-effectful tools against a sandbox or fixtures.

Run applicable lint, type checks, tests, and static analysis from the real repository. Then compare baseline and Astra runs on task success, schema validity, tool correctness, p50/p95 latency, and cost per successful task. Use repeated samples for variable model behavior. Agree workload-specific latency and spending thresholds before rollout; no existing limits are available here.

Standard token prices currently list $10/M input, $1/M cached input, $12.50/M cache writes, and $50/M output. Above 272K input tokens, higher rates apply. Account-specific access and throughput remain unverified. [Official Astra model specification](https://developers.openai.com/api/docs/models/gpt-6-astra)

Exit condition: all critical contract cases pass, no duplicate tool side effects occur, and quality, latency, and cost satisfy the recorded thresholds.

## 5. Roll out and retain rollback

Start in staging, then a small production cohort. Increase traffic only after sufficient representative samples meet the evaluation gates. Monitor errors, task failures, latency, tokens, and spending by model and workload.

Rollback on critical correctness failures, duplicate actions, or agreed operational threshold breaches. Restore both the prior deployment/configuration and its compatible conversation handling; verify a session created during the canary can be continued or safely restarted.

Exit condition: the intended workloads run on Astra, rollback is exercised, and operating documentation reflects the deployed configuration.

## Planning limits

Only this plan was added. No application migration, API calls, or application tests were performed. Implementation files, effort estimates, baseline quality, and rollout thresholds require the actual source and workload data.
