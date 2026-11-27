package com.expensetracker.app;

import android.content.Intent;
import android.os.Bundle;

import com.expensetracker.app.autotrack.AutoTrackPlugin;
import com.expensetracker.app.autotrack.AutoTrackStore;
import com.expensetracker.app.autotrack.Notifier;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(AutoTrackPlugin.class);
        super.onCreate(savedInstanceState);
        rememberRoute(getIntent());
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        rememberRoute(intent);
        // The page is already open: tell it to look for the route now
        if (getBridge() != null && intent != null && intent.hasExtra(Notifier.EXTRA_ROUTE)) {
            getBridge().triggerWindowJSEvent("autotrackRoute");
        }
    }

    /** A tapped auto-tracking notification opens its page; the web app picks the route up with AutoTrack.takeRoute(). */
    private void rememberRoute(Intent intent) {
        if (intent != null && intent.hasExtra(Notifier.EXTRA_ROUTE)) {
            AutoTrackStore.setRoute(this, intent.getStringExtra(Notifier.EXTRA_ROUTE));
        }
    }
}
