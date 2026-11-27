package com.expensetracker.app.autotrack;

import android.Manifest;
import android.content.ComponentName;
import android.content.Intent;
import android.database.Cursor;
import android.net.Uri;
import android.os.Build;
import android.os.PowerManager;
import android.provider.Settings;
import android.provider.Telephony;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.PermissionState;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;
import com.getcapacitor.annotation.Permission;
import com.getcapacitor.annotation.PermissionCallback;

/**
 * What the Auto-tracking page uses on the phone: permissions, linking the phone (where to send and its token),
 * reading past bank SMS for a first scan, and sending anything waiting.
 */
@CapacitorPlugin(
    name = "AutoTrack",
    permissions = {
        @Permission(alias = "sms", strings = { Manifest.permission.READ_SMS, Manifest.permission.RECEIVE_SMS }),
        @Permission(alias = "notify", strings = { "android.permission.POST_NOTIFICATIONS" }),
    }
)
public class AutoTrackPlugin extends Plugin {
    private static final int MAX_SCAN = 5000;

    @PluginMethod
    public void status(PluginCall call) {
        call.resolve(statusObject());
    }

    private JSObject statusObject() {
        JSObject o = new JSObject();
        o.put("sms", getPermissionState("sms") == PermissionState.GRANTED);
        o.put("postNotifications", Build.VERSION.SDK_INT < 33 || getPermissionState("notify") == PermissionState.GRANTED);
        o.put("notificationAccess", notificationAccess());
        o.put("enabled", AutoTrackStore.enabled(getContext()));
        o.put("smsOn", AutoTrackStore.smsOn(getContext()));
        o.put("notificationsOn", AutoTrackStore.notificationsOn(getContext()));
        o.put("linked", AutoTrackStore.linked(getContext()));
        o.put("queued", AutoTrackStore.queued(getContext()));
        o.put("sdk", Build.VERSION.SDK_INT);
        o.put("batteryFree", batteryFree());
        return o;
    }

    /** Android won't put the app to sleep to save battery (which delays or stops sending in the background). */
    private boolean batteryFree() {
        if (Build.VERSION.SDK_INT < 23) return true;
        PowerManager pm = (PowerManager) getContext().getSystemService(android.content.Context.POWER_SERVICE);
        return pm == null || pm.isIgnoringBatteryOptimizations(getContext().getPackageName());
    }

    @Override
    public void load() {
        rebindListener();
    }

    /**
     * After the app is updated (or killed by the system) Android can leave the notification listener unbound while
     * "Notification access" still shows as on, and then no notification arrives. Ask Android to connect it again.
     */
    private void rebindListener() {
        if (Build.VERSION.SDK_INT < 24 || !AutoTrackStore.notificationsOn(getContext()) || !notificationAccess()) return;
        try {
            android.service.notification.NotificationListenerService.requestRebind(
                new ComponentName(getContext(), BankNotificationListener.class));
        } catch (RuntimeException ignored) {
            // Already bound, or access was just taken away
        }
    }

    private boolean notificationAccess() {
        String flat = Settings.Secure.getString(getContext().getContentResolver(), "enabled_notification_listeners");
        if (flat == null) return false;
        String mine = new ComponentName(getContext(), BankNotificationListener.class).flattenToString();
        for (String part : flat.split(":")) if (part.equals(mine)) return true;
        return false;
    }

    @PluginMethod
    public void requestSms(PluginCall call) {
        if (getPermissionState("sms") == PermissionState.GRANTED) {
            call.resolve(statusObject());
        } else {
            requestPermissionForAlias("sms", call, "afterPermission");
        }
    }

    @PluginMethod
    public void requestPostNotifications(PluginCall call) {
        if (Build.VERSION.SDK_INT < 33 || getPermissionState("notify") == PermissionState.GRANTED) {
            call.resolve(statusObject());
        } else {
            requestPermissionForAlias("notify", call, "afterPermission");
        }
    }

    /** Everything auto-tracking asks Android for, in one go: reading SMS and showing notifications. */
    @PluginMethod
    public void requestAll(PluginCall call) {
        boolean sms = getPermissionState("sms") == PermissionState.GRANTED;
        boolean notify = Build.VERSION.SDK_INT < 33 || getPermissionState("notify") == PermissionState.GRANTED;
        if (sms && notify) {
            call.resolve(statusObject());
        } else if (sms) {
            requestPermissionForAlias("notify", call, "afterPermission");
        } else if (notify) {
            requestPermissionForAlias("sms", call, "afterPermission");
        } else {
            requestPermissionForAliases(new String[] { "sms", "notify" }, call, "afterPermission");
        }
    }

    /** Ask Android to let the app keep sending in the background (the "Allow / Deny" battery dialog). */
    @PluginMethod
    public void requestBatteryFree(PluginCall call) {
        if (batteryFree()) {
            call.resolve(statusObject());
            return;
        }
        Intent intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getContext().getPackageName()));
        try {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
        } catch (Exception e) {
            // Some phones don't have this dialog: the battery settings list instead
            startSettings(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS), call);
            return;
        }
        call.resolve(statusObject());
    }

    @PermissionCallback
    private void afterPermission(PluginCall call) {
        call.resolve(statusObject());
    }

    /** Android's "Notification access" screen, where the user lets the app read bank-app notifications. */
    @PluginMethod
    public void openNotificationAccess(PluginCall call) {
        startSettings(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS), call);
    }

    /** This app's "App info" screen: on Android 13+ an app installed outside the Play Store needs
     * ⋮ → "Allow restricted settings" there before Android lets it read SMS or notifications. */
    @PluginMethod
    public void openAppSettings(PluginCall call) {
        startSettings(new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + getContext().getPackageName())), call);
    }

    private void startSettings(Intent intent, PluginCall call) {
        try {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            getContext().startActivity(intent);
            call.resolve();
        } catch (Exception e) {
            call.reject("Couldn't open the settings screen");
        }
    }

    /** Link (token given), change what is read, or unlink (clearToken). */
    @PluginMethod
    public void configure(PluginCall call) {
        boolean clear = Boolean.TRUE.equals(call.getBoolean("clearToken", false));
        String token = call.getString("token");
        AutoTrackStore.configure(getContext(), call.getString("apiBase"), clear ? null : token,
            Boolean.TRUE.equals(call.getBoolean("enabled", false)), Boolean.TRUE.equals(call.getBoolean("sms", true)),
            Boolean.TRUE.equals(call.getBoolean("notifications", false)), clear || token != null);
        rebindListener();
        call.resolve(statusObject());
    }

    /** Bank SMS already in the inbox between `since` and `until` (milliseconds), for a first scan. */
    @PluginMethod
    public void readInbox(PluginCall call) {
        if (getPermissionState("sms") != PermissionState.GRANTED) {
            call.reject("Allow the app to read SMS first");
            return;
        }
        final long since = call.getLong("since", 0L);
        final long until = call.getLong("until", System.currentTimeMillis());
        new Thread(() -> {
            JSArray found = new JSArray();
            int scanned = 0;
            String[] cols = { Telephony.Sms.ADDRESS, Telephony.Sms.BODY, Telephony.Sms.DATE };
            try (Cursor cur = getContext().getContentResolver().query(
                Telephony.Sms.Inbox.CONTENT_URI, cols, Telephony.Sms.DATE + " >= ? AND " + Telephony.Sms.DATE + " <= ?",
                new String[] { String.valueOf(since), String.valueOf(until) }, Telephony.Sms.DATE + " ASC")) {
                while (cur != null && cur.moveToNext() && found.length() < MAX_SCAN) {
                    scanned++;
                    String from = cur.getString(0), body = cur.getString(1);
                    if (!BankFilter.isBankSms(from, body)) continue;
                    JSObject m = new JSObject();
                    m.put("sender", from);
                    m.put("body", body.length() > 2000 ? body.substring(0, 2000) : body);
                    m.put("received_ms", cur.getLong(2));
                    m.put("source", "SMS");
                    found.put(m);
                }
            } catch (Exception e) {
                call.reject("Couldn't read the SMS inbox: " + e.getMessage());
                return;
            }
            JSObject out = new JSObject();
            out.put("messages", found);
            out.put("scanned", scanned);
            out.put("limited", found.length() >= MAX_SCAN);
            call.resolve(out);
        }, "autotrack-scan").start();
    }

    /** Send whatever is waiting now (e.g. when the app opens). */
    @PluginMethod
    public void flush(PluginCall call) {
        Uploader.flushAsync(getContext(), () -> {
            // Some couldn't go now (offline): WorkManager sends them once the phone is online
            if (AutoTrackStore.queued(getContext()) > 0) UploadWorker.schedule(getContext());
            call.resolve(statusObject());
        });
    }

    /** The page a tapped notification asked for, once. */
    @PluginMethod
    public void takeRoute(PluginCall call) {
        JSObject o = new JSObject();
        o.put("route", AutoTrackStore.takeRoute(getContext()));
        call.resolve(o);
    }
}
