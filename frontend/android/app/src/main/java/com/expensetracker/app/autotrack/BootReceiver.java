package com.expensetracker.app.autotrack;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;

/** After a restart: send anything still waiting from before (e.g. messages that arrived while offline). */
public class BootReceiver extends BroadcastReceiver {
    @Override
    public void onReceive(Context context, Intent intent) {
        if (!Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction()) || !AutoTrackStore.enabled(context)) return;
        if (AutoTrackStore.queued(context) == 0) return;
        PendingResult pending = goAsync();
        Uploader.flushAsync(context, pending::finish);
    }
}
