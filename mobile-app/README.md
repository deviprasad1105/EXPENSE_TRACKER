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

Download the newer APK and install it over the old one. You stay signed in, and your data is on the server, so
nothing is lost.

## Good to know

- You need an internet connection, because the app reads and saves your data on the live site.
- Reports and exports open the share sheet: save them to Files or Drive, or open them in Excel or a PDF viewer.
- iPhone: there is no iPhone app yet. Open the website in Safari and use **Share → Add to Home Screen**.

For developers: the README in the repository root explains how to build a new APK (`npm run apk` in `frontend/`).
After building, copy `frontend/android/app/build/outputs/apk/debug/app-debug.apk` here as `ExpenseTracker.apk`.
