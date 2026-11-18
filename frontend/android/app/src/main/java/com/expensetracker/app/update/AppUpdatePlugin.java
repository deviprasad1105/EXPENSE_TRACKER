package com.expensetracker.app.update;

import android.content.Intent;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.Build;
import android.provider.Settings;

import androidx.core.content.FileProvider;

import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Updates the app from inside it: downloads the newer APK into the app's cache and opens Android's installer.
 * Android asks the user once to allow "Install unknown apps" for Expense Tracker, and the installer itself refuses an
 * APK that isn't signed with the same key, so only our own releases can replace the app.
 */
@CapacitorPlugin(name = "AppUpdate")
public class AppUpdatePlugin extends Plugin {
    private static final String APK_NAME = "ExpenseTracker-update.apk";
    private volatile boolean downloading = false;

    /** This app's version, and whether it may open the installer without asking first. */
    @PluginMethod
    public void info(PluginCall call) {
        JSObject o = new JSObject();
        try {
            PackageInfo p = getContext().getPackageManager().getPackageInfo(getContext().getPackageName(), 0);
            o.put("versionCode", Build.VERSION.SDK_INT >= 28 ? p.getLongVersionCode() : p.versionCode);
            o.put("versionName", p.versionName);
        } catch (PackageManager.NameNotFoundException ignored) {
            // Our own package is always there
        }
        o.put("canInstall", canInstall());
        call.resolve(o);
    }

    /** Open Android's "Install unknown apps" switch for Expense Tracker. */
    @PluginMethod
    public void openInstallSettings(PluginCall call) {
        if (Build.VERSION.SDK_INT >= 26) {
            Intent i = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + getContext().getPackageName()));
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(i);
        }
        call.resolve();
    }

    /**
     * Download `url` and open the installer. Emits "progress" events ({ percent, bytes, total }).
     * Rejects when installs aren't allowed yet (code NOT_ALLOWED: call openInstallSettings), or when the file isn't a
     * newer Expense Tracker.
     */
    @PluginMethod
    public void downloadAndInstall(PluginCall call) {
        String url = call.getString("url");
        if (url == null || !url.startsWith("https://")) {
            call.reject("A secure download link is needed", "BAD_URL");
            return;
        }
        if (!canInstall()) {
            call.reject("Allow Expense Tracker to install updates first", "NOT_ALLOWED");
            return;
        }
        if (downloading) {
            call.reject("The update is already downloading", "BUSY");
            return;
        }
        downloading = true;
        new Thread(() -> {
            try {
                File apk = download(url);
                checkIsNewerUs(apk);
                openInstaller(apk);
                call.resolve();
            } catch (Exception e) {
                call.reject(e.getMessage() == null ? "The update couldn't be downloaded" : e.getMessage(), "FAILED");
            } finally {
                downloading = false;
            }
        }, "app-update").start();
    }

    private boolean canInstall() {
        return Build.VERSION.SDK_INT < 26 || getContext().getPackageManager().canRequestPackageInstalls();
    }

    private File download(String link) throws Exception {
        File dir = new File(getContext().getCacheDir(), "updates");
        if (!dir.exists() && !dir.mkdirs()) throw new Exception("No room to save the update");
        File out = new File(dir, APK_NAME);
        HttpURLConnection conn = null;
        try {
            URL url = new URL(link);
            // GitHub answers the download link with a redirect to its file server: follow it (https only)
            for (int hops = 0; ; hops++) {
                conn = (HttpURLConnection) url.openConnection();
                conn.setInstanceFollowRedirects(false);
                conn.setConnectTimeout(20000);
                conn.setReadTimeout(60000);
                int code = conn.getResponseCode();
                if (code >= 300 && code < 400 && hops < 5) {
                    String next = conn.getHeaderField("Location");
                    conn.disconnect();
                    url = new URL(url, next);
                    if (!"https".equals(url.getProtocol())) throw new Exception("The download link isn't secure");
                    continue;
                }
                if (code != 200) throw new Exception("The download failed (HTTP " + code + "). Try again later.");
                break;
            }
            long total = conn.getContentLengthLong();
            long done = 0;
            int lastPercent = -1;
            byte[] buf = new byte[64 * 1024];
            try (InputStream in = conn.getInputStream(); OutputStream os = new FileOutputStream(out)) {
                int n;
                while ((n = in.read(buf)) > 0) {
                    os.write(buf, 0, n);
                    done += n;
                    int percent = total > 0 ? (int) (done * 100 / total) : -1;
                    if (percent != lastPercent) {
                        lastPercent = percent;
                        JSObject p = new JSObject();
                        p.put("percent", percent);
                        p.put("bytes", done);
                        p.put("total", total);
                        notifyListeners("progress", p);
                    }
                }
            }
            if (total > 0 && done != total) throw new Exception("The download was cut off. Try again.");
            return out;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    /** The file must be Expense Tracker and newer than this app (Android checks the signature when installing). */
    private void checkIsNewerUs(File apk) throws Exception {
        PackageManager pm = getContext().getPackageManager();
        PackageInfo theirs = pm.getPackageArchiveInfo(apk.getAbsolutePath(), 0);
        if (theirs == null || !getContext().getPackageName().equals(theirs.packageName)) {
            throw new Exception("The downloaded file isn't Expense Tracker");
        }
        PackageInfo mine = pm.getPackageInfo(getContext().getPackageName(), 0);
        long theirCode = Build.VERSION.SDK_INT >= 28 ? theirs.getLongVersionCode() : theirs.versionCode;
        long myCode = Build.VERSION.SDK_INT >= 28 ? mine.getLongVersionCode() : mine.versionCode;
        if (theirCode <= myCode) throw new Exception("You already have the newest version");
    }

    private void openInstaller(File apk) {
        Uri uri = FileProvider.getUriForFile(getContext(), getContext().getPackageName() + ".fileprovider", apk);
        Intent i = new Intent(Intent.ACTION_VIEW);
        i.setDataAndType(uri, "application/vnd.android.package-archive");
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        getContext().startActivity(i);
    }
}
