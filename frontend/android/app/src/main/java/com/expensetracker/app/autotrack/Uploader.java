package com.expensetracker.app.autotrack;

import android.content.Context;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/** Sends waiting messages to the server with the phone's token, and tells the user what each one became. */
public final class Uploader {
    private static final int BATCH = 200;
    private static final Object RUNNING = new Object();

    private Uploader() {}

    /** Send in the background; `done` (may be null) runs afterwards, e.g. to finish a broadcast receiver. */
    public static void flushAsync(Context context, Runnable done) {
        final Context app = context.getApplicationContext();
        new Thread(() -> {
            try {
                flush(app);
            } finally {
                if (done != null) done.run();
            }
        }, "autotrack-upload").start();
    }

    public static void flush(Context c) {
        synchronized (RUNNING) {
            String base = AutoTrackStore.apiBase(c), token = AutoTrackStore.token(c);
            if (base == null || token == null) return;
            while (AutoTrackStore.queued(c) > 0) {
                JSONArray batch = AutoTrackStore.take(c, BATCH);
                int code = send(c, base, token, batch);
                if (code == 401) {
                    // Unlinked on the server (or a new phone was linked): keep nothing for a token that no longer works
                    AutoTrackStore.unlink(c);
                    return;
                }
                if (code < 200 || code >= 300) {
                    // Offline or the server is busy: keep them for next time (a new message, the app opening, a restart)
                    if (code != 400 && code != 422) AutoTrackStore.requeue(c, batch);
                    return;
                }
            }
        }
    }

    private static int send(Context c, String base, String token, JSONArray batch) {
        HttpURLConnection conn = null;
        try {
            JSONObject body = new JSONObject().put("messages", batch);
            conn = (HttpURLConnection) new URL(base.replaceAll("/+$", "") + "/api/autotrack/device/messages").openConnection();
            conn.setRequestMethod("POST");
            conn.setConnectTimeout(15000);
            conn.setReadTimeout(30000);
            conn.setDoOutput(true);
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
            conn.setRequestProperty("X-Device-Token", token);
            try (OutputStream out = conn.getOutputStream()) {
                out.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
            int code = conn.getResponseCode();
            if (code >= 200 && code < 300) {
                try (InputStream in = conn.getInputStream()) {
                    Notifier.announce(c, new JSONObject(read(in)).optJSONArray("results"));
                } catch (Exception ignored) {
                    // Recorded on the server either way; only the notification is skipped
                }
            }
            return code;
        } catch (Exception e) {
            return -1;
        } finally {
            if (conn != null) conn.disconnect();
        }
    }

    private static String read(InputStream in) throws java.io.IOException {
        ByteArrayOutputStream buf = new ByteArrayOutputStream();
        byte[] chunk = new byte[8192];
        int n;
        while ((n = in.read(chunk)) > 0) buf.write(chunk, 0, n);
        return buf.toString("UTF-8");
    }
}
