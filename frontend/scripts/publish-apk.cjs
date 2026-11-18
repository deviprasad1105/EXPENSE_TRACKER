#!/usr/bin/env node
/**
 * Build a signed release APK with the current web code and publish it to mobile-app/ (npm run publish:apk).
 *
 *   1. Raises the version in app-version.json (so phones see "new version available" and Android installs it over
 *      the old one). Pass --no-bump to rebuild the same version.
 *   2. Builds the web app for Android, copies it into the native project (cap sync) and runs Gradle assembleRelease.
 *   3. Copies the APK to mobile-app/ExpenseTracker.apk and writes mobile-app/version.json.
 *
 * Installed apps read mobile-app/version.json when they open and offer the update ("Later" allowed). To make older
 * apps update before they can be used (e.g. after a change the old app can't work with):
 *   npm run publish:apk -- --require      every older version must update
 *   npm run publish:apk -- --min=5        versions below 5 must update
 * Without these, the previous minimum is kept.
 *
 * Needs the release signing key: ~/.android-keys/keystore.properties (or EXPENSE_TRACKER_KEYSTORE), see mobile-app/README.md.
 * Run it after merging web changes, so the app shows the same features as the website. Then commit mobile-app/.
 */
const { execSync } = require('node:child_process')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')

const front = path.resolve(__dirname, '..')
const versionFile = path.join(front, 'app-version.json')
const outDir = path.resolve(front, '..', 'mobile-app')
const keystore = process.env.EXPENSE_TRACKER_KEYSTORE || path.join(os.homedir(), '.android-keys', 'keystore.properties')

if (!fs.existsSync(keystore)) {
  console.error(`No signing key settings at ${keystore}.\nAsk the app's maintainer for the key file; see mobile-app/README.md.`)
  process.exit(1)
}

const version = JSON.parse(fs.readFileSync(versionFile, 'utf8'))
if (!process.argv.includes('--no-bump')) {
  version.versionCode += 1
  const [major, minor] = String(version.versionName).split('.').map(Number)
  version.versionName = `${major}.${(minor || 0) + 1}`
  fs.writeFileSync(versionFile, JSON.stringify(version, null, 2) + '\n')
}
console.log(`Building app ${version.versionName} (${version.versionCode})`)

const run = (cmd, cwd = front) => execSync(cmd, { cwd, stdio: 'inherit' })
run('npx vite build --mode android')
run('npx cap sync android')
run(process.platform === 'win32' ? 'gradlew.bat assembleRelease' : 'sh gradlew assembleRelease', path.join(front, 'android'))

const apk = path.join(front, 'android', 'app', 'build', 'outputs', 'apk', 'release', 'app-release.apk')
if (!fs.existsSync(apk)) {
  console.error(`The signed APK wasn't made (${apk} is missing). Check the signing key settings.`)
  process.exit(1)
}
// The oldest version still allowed: --require (this one), --min=N, else what was published before
let minVersionCode = 0
try {
  minVersionCode = Number(JSON.parse(fs.readFileSync(path.join(outDir, 'version.json'), 'utf8')).minVersionCode) || 0
} catch {
  // First publish with a minimum
}
const minArg = process.argv.find((a) => a.startsWith('--min='))
if (process.argv.includes('--require')) minVersionCode = version.versionCode
else if (minArg) minVersionCode = Number(minArg.slice(6))
if (!(minVersionCode >= 0 && minVersionCode <= version.versionCode)) {
  console.error(`--min must be between 0 and ${version.versionCode}`)
  process.exit(1)
}

fs.copyFileSync(apk, path.join(outDir, 'ExpenseTracker.apk'))
fs.writeFileSync(path.join(outDir, 'version.json'), JSON.stringify({ ...version, minVersionCode }, null, 2) + '\n')
console.log(`\nPublished mobile-app/ExpenseTracker.apk (${version.versionName}).` +
  (minVersionCode ? ` Versions below ${minVersionCode} must update.` : '') + ' Commit mobile-app/ and frontend/app-version.json.')
