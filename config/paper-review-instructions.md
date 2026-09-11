# Native Grok Bot: continuous paper review

Routine: **Zmarty paper — continuous review & reports**. Run every **30 minutes**
in the installed Grok Bot, **Dan’s Senior Developer**, using **Mac Studio — Dan’s
Lab**. This instruction file is a reusable reference, not an installer or a second
routine. OpenMausBot at port 8871 is a separate app.

1. Read the active paper snapshot and live worker health:

   ```sh
   python3 /Users/davidai/ZCodeProject/ZmartyChat-paper-grid/paper_grid/experiment.py report --runtime /Users/davidai/Sandbox/grokbot/zmarty-paper-runtime
   curl --fail --silent --show-error http://127.0.0.1:8873/api/health
   python3 /Users/davidai/ZCodeProject/ZmartyChat-paper-grid/paper_grid/audits.py pending --runtime /Users/davidai/Sandbox/grokbot/zmarty-paper-runtime
   ```

2. Read `http://127.0.0.1:8873/api/analytics` for the learning lab. Compare checks, discovery, entries, additional buys, net win/loss outcomes, close reasons and add cohorts separately by account and period. Include recorded buy-rejection reasons and instrumented-versus-legacy check counts. Distinguish per-tick from mixed/coarse equity sampling; never infer absent historical rejections. State the evidence timestamp and sample size; do not treat association as a strategy improvement or change trading rules. Link <https://danslabtrader.vercel.app/#learning> when discussing this evidence. If analytics is unavailable, continue the report review and disclose the gap.

   Read `/Users/davidai/Sandbox/grokbot/vercel-publisher/publisher.json` for
   sanitized publisher status. Report a newly observed publication failure or a
   last successful publication older than 45 minutes. Do not expose authentication
   files, tokens, private paths or raw provider payloads in public content.

3. For each pending report, read its local Markdown or JSON artifact. Check this
   bot conversation for that exact report ID before posting, to avoid duplicate
   delivery after a retry. Post a factual summary here even when there are zero
   trades. Include report ID, period, both paper accounts, net results, fees,
   funding limitations, observed drawdown, risk events and data gaps. Distinguish
   simulated results from real returns and unknown values from zero.

4. Link the dashboard at <https://danslabtrader.vercel.app/>. For an individual
   report, verify that `https://danslabtrader.vercel.app/reports/REPORT_ID.html`
   exists and contains the matching report before sharing its link. If publication
   is pending, say so and deliver the local report summary without an invented
   public link. A static public snapshot is not live worker health.

5. Only after the report has actually been posted here, acknowledge its exact ID:

   ```sh
   python3 /Users/davidai/ZCodeProject/ZmartyChat-paper-grid/paper_grid/audits.py ack --paper --runtime /Users/davidai/Sandbox/grokbot/zmarty-paper-runtime --id REPORT_ID
   ```

   Replace `REPORT_ID` with the validated pending ID. If an earlier message already
   delivered that ID, verify the message before acknowledging it. Failed delivery
   stays pending; acknowledgment alone is not delivery.

6. Between due reports, remain quiet unless there is new material paper activity,
   a worker/data/audit/publishing failure, or required operator action. Report
   missing evidence honestly. Do not repeatedly post unchanged errors.

Continue indefinitely: full audits every 48 elapsed hours from 11 September 2026
02:11:55 Europe/Bucharest; daily summaries at 09:00; weekly summaries Monday at
09:00. The first full audit is due 13 September at 02:11:55. Reporting boundaries
do not freeze, close or reset the experiment. Existing risk and integrity controls
remain in effect.

This routine reviews and delivers **paper-only** evidence. Do not place live
orders, open or close exchange bots, move funds, purchase services, expose keys,
write to databases, reset accounts, tune strategies, alter sealed source, restart
services, create another routine or resume the paused legacy single-account
writer. Source now also exists in the GrokBot repository; the active service still
uses the ZmartyChat-paper-grid paths above. Changes to that arrangement require a
separate task.
