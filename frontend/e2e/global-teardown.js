import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

const screenshotsDir = fileURLToPath(new URL('./__screenshots__', import.meta.url))

// Filters that run a subset of the suite; the guard only means something on a full run.
const PARTIAL_RUN_FLAGS = ['-g', '--grep', '--grep-invert', '--project', '--last-failed', '--only-changed', '--shard']

export function isPartialRun(argv) {
  const args = argv.slice(argv.indexOf('test') + 1)
  return args.some(arg => !arg.startsWith('-') || PARTIAL_RUN_FLAGS.some(flag => arg === flag || arg.startsWith(`${flag}=`)))
}

function recordedBaselines(config) {
  const used = new Set()
  for (const project of config.projects) {
    const dir = join(project.outputDir, 'baselines')
    if (!existsSync(dir)) continue
    for (const file of readdirSync(dir)) {
      readFileSync(join(dir, file), 'utf8').split('\n').filter(Boolean).forEach(path => used.add(path))
    }
  }
  return used
}

// --update-snapshots writes baselines but never deletes one, so a renamed or
// removed screenshot would otherwise linger in the repo forever.
export default async function globalTeardown(config) {
  if (isPartialRun(process.argv)) return
  if (!existsSync(screenshotsDir)) return
  const used = recordedBaselines(config)
  const stale = readdirSync(screenshotsDir, { recursive: true })
    .filter(file => file.endsWith('.png'))
    .map(file => join(screenshotsDir, file))
    .filter(path => !used.has(path))
  if (stale.length) {
    throw new Error(`stale baselines no test uses (delete them):\n  ${stale.map(p => relative(screenshotsDir, p)).join('\n  ')}`)
  }
}
