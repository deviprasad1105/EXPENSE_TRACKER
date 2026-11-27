package com.expensetracker.app.autotrack;

import android.Manifest;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.os.Build;

import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;

import com.expensetracker.app.MainActivity;

import org.json.JSONArray;
import org.json.JSONObject;

import java.text.NumberFormat;
import java.util.Locale;

/** Phone notifications for what auto-tracking did: "₹250 at Swiggy recorded (Food)", "… tap to review", bills due. */
public final class Notifier {
    static final String CHANNEL = "autotrack";
    public static final String EXTRA_ROUTE = "autotrack_route";
    static final String ROUTE = "/auto-tracking";
    // The pop-up shows on any page; the splits list is a sensible page behind it
    static final String SPLIT_ROUTE = "/splits";

    private Notifier() {}

    static void announce(Context c, JSONArray results) {
        if (results == null || results.length() == 0 || !allowed(c)) return;
        askSplits(c, results);
        int recorded = 0, review = 0, bills = 0;
        JSONObject only = null;
        for (int i = 0; i < results.length(); i++) {
            JSONObject r = results.optJSONObject(i);
            if (r == null) continue;
            String s = r.optString("status");
            if ("CREATED".equals(s)) recorded++;
            else if ("PENDING".equals(s) || "FAILED".equals(s)) review++;
            else if ("BILL".equals(s)) bills++;
            else continue;
            only = r;
        }
        int total = recorded + review + bills;
        if (total == 0) return;
        if (total == 1) {
            show(c, (int) (only.optLong("id") % Integer.MAX_VALUE), titleFor(only), textFor(only), ROUTE);
        } else {
            StringBuilder t = new StringBuilder();
            if (recorded > 0) t.append(recorded).append(" recorded");
            if (review > 0) t.append(t.length() > 0 ? ", " : "").append(review).append(" to review");
            if (bills > 0) t.append(t.length() > 0 ? ", " : "").append(bills).append(bills == 1 ? " bill due" : " bills due");
            show(c, 1, "Auto-tracking", t.toString(), ROUTE);
        }
    }

    /** "Split ₹900 at DMart?": a payment to a shop one of the user's split groups shares. Tapping opens the question. */
    private static void askSplits(Context c, JSONArray results) {
        for (int i = 0; i < results.length(); i++) {
            JSONObject r = results.optJSONObject(i);
            if (r == null || !"ASK".equals(r.optString("split_status"))) continue;
            String label = r.optString("split_label", "");
            String shop = label, group = "your group";
            int dot = label.lastIndexOf(" · ");
            if (dot > 0) {
                shop = label.substring(0, dot);
                group = label.substring(dot + 3);
            }
            show(c, (int) ((r.optLong("id") + 500000) % Integer.MAX_VALUE), "Split " + money(r, "amount") + " at " + shop + "?",
                "Tap to split it with " + group + ", or keep it as yours.", SPLIT_ROUTE);
        }
    }

    private static String money(JSONObject r, String key) {
        double v = r.optDouble(key, Double.NaN);
        if (Double.isNaN(v)) return "";
        NumberFormat f = NumberFormat.getCurrencyInstance(new Locale("en", "IN"));
        f.setMaximumFractionDigits(v == Math.rint(v) ? 0 : 2);
        return f.format(v);
    }

    private static String titleFor(JSONObject r) {
        String s = r.optString("status");
        if ("BILL".equals(s)) return "Bill due: " + r.optString("name", "card");
        String name = r.optString("name", "");
        String amount = money(r, "amount");
        String what = "CREDIT".equals(r.optString("direction")) ? "received" : "spent";
        if ("TRANSFER".equals(r.optString("suggestion"))) what = "moved";
        return amount + " " + what + (name.isEmpty() || "null".equals(name) ? "" : " · " + name);
    }

    private static String textFor(JSONObject r) {
        String s = r.optString("status");
        if ("BILL".equals(s)) {
            String due = r.optString("due_date", "");
            return money(r, "amount") + (due.isEmpty() || "null".equals(due) ? "" : " due on " + due);
        }
        if ("CREATED".equals(s)) {
            String cat = r.optString("category", "");
            return "Recorded" + (cat.isEmpty() || "null".equals(cat) ? "" : " under " + cat) + ". Tap to change.";
        }
        return "Tap to review and record it.";
    }

    private static boolean allowed(Context c) {
        if (Build.VERSION.SDK_INT >= 33
            && ContextCompat.checkSelfPermission(c, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            return false;
        }
        return NotificationManagerCompat.from(c).areNotificationsEnabled();
    }

    private static void show(Context c, int id, String title, String text, String route) {
        if (Build.VERSION.SDK_INT >= 26) {
            NotificationManager nm = c.getSystemService(NotificationManager.class);
            if (nm != null && nm.getNotificationChannel(CHANNEL) == null) {
                NotificationChannel ch = new NotificationChannel(CHANNEL, "Auto-tracking", NotificationManager.IMPORTANCE_DEFAULT);
                ch.setDescription("Transactions read from your bank messages");
                nm.createNotificationChannel(ch);
            }
        }
        Intent open = new Intent(c, MainActivity.class)
            .putExtra(EXTRA_ROUTE, route)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        int flags = PendingIntent.FLAG_UPDATE_CURRENT | (Build.VERSION.SDK_INT >= 23 ? PendingIntent.FLAG_IMMUTABLE : 0);
        PendingIntent tap = PendingIntent.getActivity(c, id, open, flags);
        NotificationCompat.Builder b = new NotificationCompat.Builder(c, CHANNEL)
            .setSmallIcon(com.expensetracker.app.R.drawable.ic_stat_autotrack)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(new NotificationCompat.BigTextStyle().bigText(text))
            .setContentIntent(tap)
            .setAutoCancel(true);
        try {
            NotificationManagerCompat.from(c).notify(id, b.build());
        } catch (SecurityException ignored) {
            // Notifications were turned off in the meantime
        }
    }
}
