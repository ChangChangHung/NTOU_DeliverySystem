import { readFile, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import path from 'node:path'
import runIntegration from './acceptance-integration.mjs'

const input = JSON.parse(await readFile(process.argv[2], 'utf8'))
const require = createRequire(import.meta.url)
const { chromium } = require(path.join(input.playwrightModule, 'index.js'))
let browser
for (const signal of ['SIGTERM', 'SIGINT']) {
  process.once(signal, async () => {
    await browser?.close().catch(() => {})
    process.exit(1)
  })
}
try {
  browser = await chromium.launch({ channel: 'chrome', headless: true })
  const report = await runIntegration({ browser, ...input })
  if (input.audit) {
    const { default: runAudit } = await import('./acceptance-audit.mjs')
    const audit = await runAudit({ browser, ...input })
    await writeFile(path.join(input.outputDir, 'browser-audit-results.json'), JSON.stringify(audit, null, 2) + '\n', { flag: 'wx' })
    report.audit = { passed: audit.passed, total: audit.total, passed_cases: audit.passed_cases }
    report.passed = report.passed && audit.passed
  }
  report.browser = browser.version()
  report.completed_at = new Date().toISOString()
  await writeFile(path.join(input.outputDir, 'browser-results.json'), JSON.stringify(report, null, 2) + '\n', { flag: 'wx' })
  process.exitCode = report.passed ? 0 : 1
} finally {
  await browser?.close()
}
