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
        Bundle x = n.extras;
        if (x == null) return;
        CharSequence title = x.getCharSequence(Notification.EXTRA_TITLE);
        CharSequence big = x.getCharSequence(Notification.EXTRA_BIG_TEXT);
        CharSequence text = big != null ? big : x.getCharSequence(Notification.EXTRA_TEXT);
        String body = ((title == null ? "" : title + ": ") + (text == null ? "" : text)).trim();
        if (!BankFilter.isMoneyText(body)) return;
        String key = pkg + "|" + body;
        synchronized (recent) {
            if (recent.contains(key)) return;
            recent.addLast(key);
            while (recent.size() > RECENT) recent.removeFirst();
        }
        AutoTrackStore.enqueue(this, appName(pkg), body, sbn.getPostTime(), "NOTIFICATION");
        Uploader.flushAsync(this, null);
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
