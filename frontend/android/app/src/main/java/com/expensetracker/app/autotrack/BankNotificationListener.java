package com.expensetracker.app.autotrack;

import android.app.Notification;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;

import java.util.ArrayDeque;

/**
 * Notifications from bank, card and payment apps (for banks that alert in their app instead of by SMS).
 * Only apps that look like finance apps are read; chats, email and SMS apps never are. Turned on by the user in
 * Android's "Notification access" settings, and only used while "Bank app notifications" is on in the app.
 */
public class BankNotificationListener extends NotificationListenerService {
    private static final int RECENT = 50;
    // Apps re-post the same notification when it updates: send each text once
    private final ArrayDeque<String> recent = new ArrayDeque<>();

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        if (sbn == null || !AutoTrackStore.notificationsOn(this)) return;
        String pkg = sbn.getPackageName();
        if (pkg == null || pkg.equals(getPackageName()) || !BankFilter.isFinanceApp(pkg)) return;
        Notification n = sbn.getNotification();
        if (n == null || (n.flags & Notification.FLAG_ONGOING_EVENT) != 0 || (n.flags & Notification.FLAG_GROUP_SUMMARY) != 0) return;
        String body = text(n);
        if (body.isEmpty() || !BankFilter.isMoneyText(body)) return;
        String key = pkg + "|" + body;
        synchronized (recent) {
            if (recent.contains(key)) return;
            recent.addLast(key);
            while (recent.size() > RECENT) recent.removeFirst();
        }
        AutoTrackStore.enqueue(this, appName(pkg), body, sbn.getPostTime(), "NOTIFICATION");
        Uploader.flushAsync(this, null);
    }

    /**
     * Everything the notification says, as "Title: text". UPI apps (PhonePe, Google Pay…) put the payment in different
     * places: the title, the expanded text, inbox-style lines, the sub text, or only the ticker.
     */
    static String text(Notification n) {
        StringBuilder out = new StringBuilder();
        Bundle x = n.extras;
        if (x != null) {
            CharSequence title = x.getCharSequence(Notification.EXTRA_TITLE_BIG);
            if (title == null) title = x.getCharSequence(Notification.EXTRA_TITLE);
            CharSequence big = x.getCharSequence(Notification.EXTRA_BIG_TEXT);
            CharSequence text = big != null ? big : x.getCharSequence(Notification.EXTRA_TEXT);
            add(out, title);
            add(out, text);
            CharSequence[] lines = x.getCharSequenceArray(Notification.EXTRA_TEXT_LINES);
            if (lines != null) for (CharSequence line : lines) add(out, line);
            add(out, x.getCharSequence(Notification.EXTRA_SUB_TEXT));
            add(out, x.getCharSequence(Notification.EXTRA_SUMMARY_TEXT));
        }
        // Apps that draw their own layout often leave the fields above empty but still set the ticker
        if (!BankFilter.isMoneyText(out.toString())) add(out, n.tickerText);
        return out.toString().trim();
    }

    private static void add(StringBuilder out, CharSequence part) {
        if (part == null) return;
        String s = part.toString().trim();
        if (s.isEmpty() || out.indexOf(s) >= 0) return;
        if (out.length() > 0) out.append(out.indexOf(":") < 0 ? ": " : ". ");
        out.append(s);
    }

    @Override
    public void onListenerDisconnected() {
        // Android unbinds the listener at times (e.g. after the app is updated): ask to be connected again
        if (android.os.Build.VERSION.SDK_INT >= 24) {
            requestRebind(new android.content.ComponentName(this, BankNotificationListener.class));
        }
    }

    private String appName(String pkg) {
        try {
            PackageManager pm = getPackageManager();
            ApplicationInfo info = pm.getApplicationInfo(pkg, 0);
            return String.valueOf(pm.getApplicationLabel(info));
        } catch (PackageManager.NameNotFoundException e) {
            return pkg;
        }
    }
}
