package com.expensetracker.app.autotrack;

import android.content.Context;
import android.content.SharedPreferences;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

/**
 * What the background parts need without the app open: where to send, the phone's token, what to read, and messages
 * waiting to be sent (kept until the server has them, so nothing is lost while offline).
 */
public final class AutoTrackStore {
    private static final String PREFS = "autotrack";
    private static final int MAX_QUEUE = 2000;
    private static final Object LOCK = new Object();

    private AutoTrackStore() {}

    private static SharedPreferences prefs(Context c) {
        return c.getApplicationContext().getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    public static void configure(Context c, String apiBase, String token, boolean enabled, boolean sms, boolean notifications,
                                 boolean setToken) {
        SharedPreferences.Editor e = prefs(c).edit()
            .putBoolean("enabled", enabled).putBoolean("sms", sms).putBoolean("notifications", notifications);
        if (apiBase != null) e.putString("api_base", apiBase);
        if (setToken) {
            if (token == null) e.remove("token"); else e.putString("token", token);
        }
        e.apply();
    }

    public static boolean linked(Context c) { return prefs(c).getString("token", null) != null; }
    public static boolean enabled(Context c) { return prefs(c).getBoolean("enabled", false) && linked(c); }
    public static boolean smsOn(Context c) { return enabled(c) && prefs(c).getBoolean("sms", true); }
    public static boolean notificationsOn(Context c) { return enabled(c) && prefs(c).getBoolean("notifications", false); }
    public static String apiBase(Context c) { return prefs(c).getString("api_base", null); }
    public static String token(Context c) { return prefs(c).getString("token", null); }

    /** The phone was unlinked on the server: stop until the app links it again. */
    public static void unlink(Context c) {
        prefs(c).edit().remove("token").putBoolean("enabled", false).apply();
    }

    public static void enqueue(Context c, String sender, String body, long receivedMs, String source) {
        synchronized (LOCK) {
            JSONArray q = queue(c);
            try {
                JSONObject m = new JSONObject();
                m.put("sender", sender == null ? JSONObject.NULL : sender);
                m.put("body", body.length() > 2000 ? body.substring(0, 2000) : body);
                m.put("received_ms", receivedMs);
                m.put("source", source);
                q.put(m);
            } catch (JSONException ignored) {
                return;
            }
            // Oldest dropped first if the phone was offline for a very long time
            while (q.length() > MAX_QUEUE) q.remove(0);
            prefs(c).edit().putString("queue", q.toString()).apply();
        }
    }

    /** Take up to `max` waiting messages out of the queue (put them back with requeue if sending fails). */
    public static JSONArray take(Context c, int max) {
        synchronized (LOCK) {
            JSONArray q = queue(c), out = new JSONArray(), rest = new JSONArray();
            for (int i = 0; i < q.length(); i++) {
                (i < max ? out : rest).put(q.opt(i));
            }
            prefs(c).edit().putString("queue", rest.toString()).apply();
            return out;
        }
    }

    public static void requeue(Context c, JSONArray messages) {
        synchronized (LOCK) {
            JSONArray q = queue(c), merged = new JSONArray();
            for (int i = 0; i < messages.length(); i++) merged.put(messages.opt(i));
            for (int i = 0; i < q.length(); i++) merged.put(q.opt(i));
            while (merged.length() > MAX_QUEUE) merged.remove(0);
            prefs(c).edit().putString("queue", merged.toString()).apply();
        }
    }

    public static int queued(Context c) {
        synchronized (LOCK) {
            return queue(c).length();
        }
    }

    private static JSONArray queue(Context c) {
        try {
            return new JSONArray(prefs(c).getString("queue", "[]"));
        } catch (JSONException e) {
            return new JSONArray();
        }
    }

    /** A page the app should open next (set when a notification is tapped). */
    public static void setRoute(Context c, String route) { prefs(c).edit().putString("route", route).apply(); }

    public static String takeRoute(Context c) {
        String r = prefs(c).getString("route", null);
        if (r != null) prefs(c).edit().remove("route").apply();
        return r;
    }
}
