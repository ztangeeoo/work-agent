/**
 * 智能体流水线归并逻辑回归测试
 * 运行：node --experimental-strip-types tests/test_stages.ts
 */
import {
  emptyRows, isTraceFinished, reduceStageEvents, sumStageMs,
  type StageEvent,
} from '../web/src/lib/stageReducer.ts';

let pass = 0, fail = 0;
function ok(cond: boolean, name: string) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name); }
}

const ev = (stage: string, status: StageEvent['status'], extra: Partial<StageEvent> = {}): StageEvent => ({
  stage, status, ts: 1_700_000_000_000,
  label: ({ planner: '策划官', interviewer: '面试官', grader: '评分官' } as Record<string, string>)[stage] || stage,
  icon: '🧭', ...extra,
});

console.log('\n[1] 空事件 → 三个环节均为 idle');
{
  const rows = reduceStageEvents([]);
  ok(rows.length === 3, '三个环节');
  ok(rows.every(r => r.status === 'idle'), '全部 idle');
  ok(rows.map(r => r.key).join(',') === 'planner,interviewer,grader', '顺序固定');
  ok(!isTraceFinished(rows), '未结束');
}

console.log('\n[2] 完整出题流水线：策划官 → 面试官，评分官跳过');
{
  const rows = reduceStageEvents([
    ev('planner', 'running'),
    ev('planner', 'done', { ms: 2100, detail: '大纲 120 字' }),
    ev('grader', 'skipped'),
    ev('interviewer', 'running'),
    ev('interviewer', 'tool', { detail: '查阅题库 Redis' }),
    ev('interviewer', 'done', { ms: 3400 }),
  ]);
  const byKey = Object.fromEntries(rows.map(r => [r.key, r]));
  ok(byKey.planner.status === 'done', '策划官 done');
  ok(byKey.planner.ms === 2100, '策划官耗时 2100ms');
  ok(byKey.planner.detail === '大纲 120 字', '保留 detail');
  ok(byKey.interviewer.status === 'done', '面试官最终 done（覆盖 tool）');
  ok(byKey.interviewer.ms === 3400, '面试官耗时 3400ms');
  ok(byKey.interviewer.detail === '查阅题库 Redis', 'tool 的 detail 被保留');
  ok(byKey.grader.status === 'skipped', '评分官 skipped');
  ok(isTraceFinished(rows), '整轮已结束');
  ok(sumStageMs(rows) === 5500, '总耗时 5500ms');
}

console.log('\n[3] 评分分支：策划官/面试官跳过，评分官执行');
{
  const rows = reduceStageEvents([
    ev('planner', 'skipped'),
    ev('interviewer', 'skipped'),
    ev('grader', 'running'),
    ev('grader', 'done', { ms: 17012 }),
  ]);
  const byKey = Object.fromEntries(rows.map(r => [r.key, r]));
  ok(byKey.grader.status === 'done' && byKey.grader.ms === 17012, '评分官完成 17s');
  ok(byKey.planner.status === 'skipped' && byKey.interviewer.status === 'skipped', '前两环节跳过');
  ok(isTraceFinished(rows), '已结束');
}

console.log('\n[4] 未完成的轮次不算结束（用于「已中止」判定）');
{
  const rows = reduceStageEvents([ev('planner', 'running'), ev('planner', 'done', { ms: 900 })]);
  ok(!isTraceFinished(rows), '面试官仍 idle → 未结束');
  ok(emptyRows().every(r => r.status === 'idle'), 'emptyRows 全 idle');
}

console.log('\n[5] 未知环节会被追加，不丢数据');
{
  const rows = reduceStageEvents([ev('researcher', 'done', { ms: 400 })]);
  ok(rows.length === 4, '追加了第 4 个环节');
  ok(rows[3].key === 'researcher' && rows[3].status === 'done', '未知环节状态正确');
  ok(rows[3].label === 'researcher', '未给 label 时用 key 兜底');
}

console.log('\n[6] 错误状态参与结束判定');
{
  const rows = reduceStageEvents([
    ev('planner', 'done', { ms: 100 }),
    ev('interviewer', 'error', { detail: '模型 401' }),
    ev('grader', 'skipped'),
  ]);
  ok(isTraceFinished(rows), '含 error 也算结束');
  ok(rows.find(r => r.key === 'interviewer')!.detail === '模型 401', '错误详情保留');
}

console.log(`\n结果：${pass} 通过 / ${fail} 失败`);
if (fail) process.exitCode = 1;
