import fs from 'node:fs';
import { requestQueueRun, runQueueBatch } from '/private/tmp/gbc-reanalyze-review-euwrvxvq/app/app/components/queue/queueBatchActions';

async function main() {
  const warnings = ['原模型配置已变化（model），按上次方式执行将使用当前配置'];
  const requests: unknown[] = [];
  const fetcher = (async (url: unknown, init: RequestInit) => {
    requests.push({ url, body: JSON.parse(String(init.body)) });
    return new Response(JSON.stringify({ job_id: 'review-queue', check_method: 'inherit', inheritance_warnings: warnings }), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    });
  }) as typeof fetch;
  const result = await runQueueBatch(
    [{ job_id: 'review-queue', filename: 'review.pdf', status: 'done' }],
    job => requestQueueRun(job, fetcher),
    () => {},
  );
  const evidence = {
    serverWarnings: warnings,
    requests,
    batchResult: result,
    warningSurvivesInResult: JSON.stringify(result).includes(warnings[0]),
  };
  fs.writeFileSync('/Volumes/workSSD/software development/GovBudgetChecker/outputs/independent_reanalyze_inherit_20261109/queue-probe-results.json', JSON.stringify(evidence, null, 2));
  console.log(JSON.stringify(evidence, null, 2));
}
main();
