package com.expensetracker.app.autotrack;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.provider.Telephony;
import android.telephony.SmsMessage;

import java.util.LinkedHashMap;
import java.util.Map;

/** Each new SMS, as it arrives (even with the app closed). Only bank messages about money are kept and sent. */
public class SmsReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Telephony.Sms.Intents.SMS_RECEIVED_ACTION.equals(intent.getAction()) || !AutoTrackStore.smsOn(context)) return;
        SmsMessage[] parts = Telephony.Sms.Intents.getMessagesFromIntent(intent);
        if (parts == null || parts.length == 0) return;
        // A long SMS arrives in parts: join them per sender
        Map<String, StringBuilder> bodies = new LinkedHashMap<>();
        long when = System.currentTimeMillis();
        for (SmsMessage p : parts) {
            if (p == null) continue;
            String from = p.getDisplayOriginatingAddress();
            String key = from == null ? "" : from;
            StringBuilder sb = bodies.get(key);
            if (sb == null) bodies.put(key, sb = new StringBuilder());
            sb.append(p.getDisplayMessageBody());
            if (p.getTimestampMillis() > 0) when = p.getTimestampMillis();
        }
        boolean any = false;
        for (Map.Entry<String, StringBuilder> e : bodies.entrySet()) {
            String body = e.getValue().toString();
            if (BankFilter.isBankSms(e.getKey(), body)) {
                AutoTrackStore.enqueue(context, e.getKey(), body, when, "SMS");
                any = true;
            }
        }
        if (any) {
            PendingResult pending = goAsync();
            Uploader.flushAsync(context, pending::finish);
        }
    }
}
