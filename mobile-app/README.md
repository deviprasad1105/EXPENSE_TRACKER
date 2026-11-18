# Expense Tracker for Android

**[Download ExpenseTracker.apk](https://github.com/deviprasad1105/EXPENSE_TRACKER/raw/main/mobile-app/ExpenseTracker.apk)** (about 18 MB, Android 5.1 or newer)

The app works with the same accounts and data as the website: sign in with your usual username and password.

## Install

1. Open the link above on your Android phone, or send the file to the phone by WhatsApp, Drive or email.
2. Tap the downloaded file.
3. If Android asks, allow **Install unknown apps** for the app you opened it from (Chrome, Files, WhatsApp…).
4. If **Play Protect** shows a warning, tap **More details → Install anyway**. The app isn't from the Play Store,
   so Android asks this once.
5. Open **Expense Tracker** from your home screen and sign in.

## Update

When a newer version is published, the app says **"New version … is available"** when you open it. Tap
**Update now**: the app downloads it and opens Android's installer; tap **Update**. The first time, Android asks you
to allow installs from Expense Tracker (**Allow from this source**). You stay signed in, and your data is on the
server, so nothing is lost. Versions before 1.4 download the update in the browser instead; install it over the old app.

Sometimes an update is **required** (the old version can't work with the website any more): the app then shows
"Update required" and can only be used after updating.

**Coming from version 1.1 or older?** Version 1.2 is signed with a new key, so Android won't install it over the old
app ("App not installed" or "package conflicts"). Uninstall Expense Tracker once, then install the new APK and sign in
again. Later updates install over it normally. If you used auto-tracking, turn on SMS and notification access again
in the app.

## Good to know

- You need an internet connection, because the app reads and saves your data on the live site.
- Reports and exports open the share sheet: save them to Files or Drive, or open them in Excel or a PDF viewer.
- iPhone: there is no iPhone app yet. Open the website in Safari and use **Share → Add to Home Screen**.

For developers: after merging web changes, run `npm run publish:apk` in `frontend/`. It raises the version, builds a
signed APK and puts it here with `version.json`; then commit this folder and `frontend/app-version.json`. It needs the
release signing key (see "Android app" in the README in the repository root).
