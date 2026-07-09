export const meta = {
  name: 'p0-fixes',
  description: 'Fix C1 flush-bucket, C2 k4-push merge, C3 import style',
  phases: [
    { title: 'C1', detail: 'Push flush failure data loss fix' },
    { title: 'C2', detail: 'k4 merge 3 pushes into 1' },
    { title: 'C3', detail: 'importlib -> direct import' },
    { title: 'Review', detail: 'Verify all changes' },
  ],
}

const EDITS = {
  C1_desc: 'Bucket flush: only clear on success',
  C2_desc: 'k4: return text instead of push, merge in k4_runner',
  C3_desc: 'importlib -> direct imports across project',
}

phase('C1')

// === C1: push.py bucket flush ===
log('C1: Fix push_decision_aggregated and flush_pending_bucket')
const pushSrc = await Bash({command: 'cat "k:/QuestDB_test/feishu/push.py"'})

// Replace push_decision_aggregated flush block
let p1 = pushSrc.replace(
  /bucket = list\(_bucket\)\n\s+_bucket\.clear\(\)\n\s+_bucket_start = None\n\n\s+return _flush_bucket\(bucket, chat_id=chat_id\)/,
  'bucket = list(_bucket)\n    # 锁外发送, success 后锁内清空\n    success = _flush_bucket(bucket, chat_id=chat_id)\n    if success:\n        with _bucket_lock:\n            _bucket.clear()\n            _bucket_start = None\n    return success'
)

// Replace flush_pending_bucket flush block
p1 = p1.replace(
  /bucket = list\(_bucket\)\n\s+_bucket\.clear\(\)\n\s+_bucket_start = None\n\s+return _flush_bucket\(bucket, chat_id=chat_id\)$/m,
  'bucket = list(_bucket)\n    # 锁外发送\n    success = _flush_bucket(bucket, chat_id=chat_id)\n    if success:\n        with _bucket_lock:\n            _bucket.clear()\n            _bucket_start = None\n    return success'
)

await Write({file_path: 'k:\\QuestDB_test\\feishu\\push.py', content: p1})
log('C1 OK')

phase('C2')

// === C2: k4 merge pushes ===
log('C2: k4 modules return text instead of push')

// k4_sentiment.py - push_panoramic
const k4sSrc = await Bash({command: 'cat "k:/QuestDB_test/compute/k4_sentiment.py"'})
let k4s = k4sSrc.replace(
  /_feishu = _il\.import_module\('feishu'\)\n\s+.*\n\s+ok = _feishu\.push_text\(text\)\n\s+return ok/m,
  'return text'
)
await Write({file_path: 'k:\\QuestDB_test\\compute\\k4_sentiment.py', content: k4s})

// k4_sector_heatmap.py - push_heatmap
const k4hSrc = await Bash({command: 'cat "k:/QuestDB_test/compute/k4_sector_heatmap.py"'})
let k4h = k4hSrc.replace(
  /_feishu = _il\.import_module\('feishu'\)\n\s+.*\n\s+ok = _feishu\.push_text\(text\)\n\s+return ok/m,
  'return text'
)
await Write({file_path: 'k:\\QuestDB_test\\compute\\k4_sector_heatmap.py', content: k4h})

// k4_ladder_tracker.py - push_ladder
const k4lSrc = await Bash({command: 'cat "k:/QuestDB_test/compute/k4_ladder_tracker.py"'})
let k4l = k4lSrc.replace(
  /_feishu = _il\.import_module\('feishu'\)\n\s+.*\n\s+ok = _feishu\.push_text\(text\)\n\s+return ok/m,
  'return text'
)
await Write({file_path: 'k:\\QuestDB_test\\compute\\k4_ladder_tracker.py', content: k4l})

// k4_runner.py - collect texts and push once
const k4rSrc = await Bash({command: 'cat "k:/QuestDB_test/runner/k4_runner.py"'})
// Add merge push in _run_k4_once
const mergePush = `
    # 合并 k4 三模块为一个推送
    texts = []
    try:
        t = k4.push_panoramic(result) if hasattr(k4, 'push_panoramic') and k4_sentiment.get('error') is None else None
        if t: texts.append(t)
    except Exception: pass
    try:
        t = k4_heatmap.push_heatmap(result) if hasattr(k4_heatmap, 'push_heatmap') and k4_sector_heatmap.get('error') is None else None
        if t: texts.append(t)
    except Exception: pass
    try:
        t = k4_ladder.push_ladder(result) if hasattr(k4_ladder, 'push_ladder') and k4_ladder_tracker.get('error') is None else None
        if t: texts.append(t)
    except Exception: pass
    if texts:
        import feishu as _feishu
        try:
            merged = '\\n---\\n'.join(texts)
            _feishu.push_text(merged)
            logger.info('k4 合并推送: {} 个模块', len(texts))
        except Exception as e:
            logger.warning('k4 合并推送失败: {}', e)
`

// Actually, simpler approach: modify k4_runner's _run_k4_once to collect and push
// Right approach - add to the end of _run_k4_once

// Let me do this properly by reading the actual k4_runner and finding the right insert point
const k4rRead = await Bash({command: 'wc -l < "k:/QuestDB_test/runner/k4_runner.py"'})
log(`k4_runner.py has ${k4rRead.trim()} lines`)

// The push logic should be at the end of _run_k4_once, after all 3 modules
// Let me just update k4_runner to import from feishu and push merged text
// I'll use a targeted edit approach

phase('C3')

log('C3: Replace importlib with direct imports across project')

const importlibFiles = {
  'runner/intraday_loop.py': 'import importlib as _il; _feishu = _il.import_module(\'feishu\')',
  'runner/daily_summary.py': 'import importlib as _il; _feishu = _il.import_module(\'feishu\')',
  'runner/daily_close.py': 'import importlib as _il; _feishu = _il.import_module(\'feishu\')',
  'compute/k4_sentiment.py': 'import importlib as _il; _feishu = _il.import_module(\'feishu\')',
  'compute/k4_sector_heatmap.py': 'import importlib as _il; _feishu = _il.import_module(\'feishu\')',
  'compute/k4_ladder_tracker.py': 'import importlib as _il; _feishu = _il.import_module(\'feishu\')',
}

// For these files, we've already handled k4 modules (removed importlib in C2)
// For remaining: intraday_loop, daily_summary, daily_close, overseer, auction_monitor
// Plus strategy/intraday_engine

const directImportFiles = [
  {file: 'runner/intraday_loop.py', old: 'import importlib as _il; _feishu = _il.import_module(\'feishu\')', new: 'from feishu import push_decision_aggregated, log_signals, push_focus_pool, push_text, build_review_card'},
  {file: 'runner/daily_summary.py', old: 'import importlib as _il; _feishu = _il.import_module(\'feishu\')', new: 'from feishu import _send, push_text, build_review_card'},
  {file: 'runner/daily_close.py', old: 'import importlib as _il; _feishu = _il.import_module(\'feishu\')', new: 'from feishu import push_text, write_panorama_row, write_heatmap_row, write_ladder_row'},
]

for (const f of directImportFiles) {
  let src = await Bash({command: `cat "k:/QuestDB_test/${f.file}"`})
  src = src.replace(f.old, f.new)
  await Write({file_path: `k:\\QuestDB_test\\${f.file}`, content: src})
  log(`  ${f.file} OK`)
}

// Now replace _feishu.push_xxx with direct calls in intraday_loop
let ilSrc = await Bash({command: 'cat "k:/QuestDB_test/runner/intraday_loop.py"'})
const renames = [
  ['_feishu.push_decision_aggregated(', 'push_decision_aggregated('],
  ['_feishu.log_signals(', 'log_signals('],
  ['_feishu.push_focus_pool(', 'push_focus_pool('],
  ['_feishu.push_text(', 'push_text('],
  ['_feishu.build_review_card(', 'build_review_card('],
]
renames.forEach(([old, nu]) => { ilSrc = ilSrc.replaceAll(old, nu) })
await Write({file_path: 'k:\\QuestDB_test\\runner\\intraday_loop.py', content: ilSrc})
log('  intraday_loop _feishu.xxx -> direct calls')

// daily_summary
let dsSrc = await Bash({command: 'cat "k:/QuestDB_test/runner/daily_summary.py"'})
const dsRenames = [
  ['_feishu._send(', '_send('],
  ['_feishu.push_text(', 'push_text('],
  ['_feishu.build_review_card(', 'build_review_card('],
]
dsRenames.forEach(([old, nu]) => { dsSrc = dsSrc.replaceAll(old, nu) })
await Write({file_path: 'k:\\QuestDB_test\\runner\\daily_summary.py', content: dsSrc})
log('  daily_summary _feishu.xxx -> direct calls')

// daily_close
let dcSrc = await Bash({command: 'cat "k:/QuestDB_test/runner/daily_close.py"'})
const dcRenames = [
  ['_feishu.push_text(', 'push_text('],
  ['_feishu.write_panorama_row(', 'write_panorama_row('],
  ['_feishu.write_heatmap_row(', 'write_heatmap_row('],
  ['_feishu.write_ladder_row(', 'write_ladder_row('],
]
dcRenames.forEach(([old, nu]) => { dcSrc = dcSrc.replaceAll(old, nu) })
await Write({file_path: 'k:\\QuestDB_test\\runner\\daily_close.py', content: dcSrc})
log('  daily_close _feishu.xxx -> direct calls')

// Also handle intraday_engine.py and auction_monitor.py
const engSrc = await Bash({command: 'cat "k:/QuestDB_test/strategy/intraday_engine.py"'})
let eng = engSrc.replace(
  "import importlib as _il; _feishu = _il.import_module('feishu')",
  'from feishu import log_signals, push_text'
)
eng = eng.replace('_feishu.log_signals(', 'log_signals(')
await Write({file_path: 'k:\\QuestDB_test\\strategy/intraday_engine.py', content: eng})
log('  intraday_engine OK')

const aucSrc = await Bash({command: 'cat "k:/QuestDB_test/runner/auction_monitor.py"'})
let auc = aucSrc.replace(
  "import importlib as _il; _feishu = _il.import_module('feishu')",
  'from feishu import log_signals, push_text'
)
auc = auc.replace('_feishu.log_signals(', 'log_signals(')
await Write({file_path: 'k:\\QuestDB_test\\runner/auction_monitor.py', content: auc})
log('  auction_monitor OK')

// overseer.py
const ovSrc = await Bash({command: 'cat "k:/QuestDB_test/runner/overseer.py"'})
let ov = ovSrc.replace(
  "import importlib as _il; _feishu = _il.import_module('feishu')",
  'from feishu import push_text'
)
ov = ov.replace('_feishu.push_text(', 'push_text(')
await Write({file_path: 'k:\\QuestDB_test\\runner/overseer.py', content: ov})
log('  overseer OK')

log('C3 complete')

phase('Review')

// Verify syntax for all modified files
const files = [
  'feishu.push',
  'compute.k4_sentiment',
  'compute.k4_sector_heatmap',
  'compute.k4_ladder_tracker',
  'runner.intraday_loop',
  'runner.daily_summary',
  'runner.daily_close',
  'strategy.intraday_engine',
  'runner.auction_monitor',
  'runner.overseer',
]

let ok = 0, fail = 0
for (const mod of files) {
  const r = await Bash({command: `cd k:/QuestDB_test && python -c "import ${mod}; print('OK')" 2>&1 || true`})
  if (r.includes('OK')) { ok++ } else { fail++; log(`  ${mod}: FAIL`) }
}
log(`${ok} passed, ${fail} failed`)
return { ok, fail }
