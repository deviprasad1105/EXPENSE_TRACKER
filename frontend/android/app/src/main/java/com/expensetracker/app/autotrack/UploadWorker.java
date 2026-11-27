package com.expensetracker.app.autotrack;

import android.content.Context;

import androidx.annotation.NonNull;
import androidx.work.BackoffPolicy;
import androidx.work.Constraints;
import androidx.work.ExistingWorkPolicy;
import androidx.work.NetworkType;
import androidx.work.OneTimeWorkRequest;
import androidx.work.WorkManager;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

import java.util.concurrent.TimeUnit;

/**
 * Sends waiting messages through Android's WorkManager: it runs as soon as there is internet, survives the app being
 * closed or the phone restarting, and retries with a growing delay while the server can't be reached. A broadcast
 * receiver's own thread is stopped after a few seconds, which is why uploads used to fail on slow networks.
 */
public class UploadWorker extends Worker {
    private static final String NAME = "autotrack-upload";

    public UploadWorker(@NonNull Context context, @NonNull WorkerParameters params) {
        super(context, params);
    }

    @NonNull
    @Override
    public Result doWork() {
        if (!AutoTrackStore.linked(getApplicationContext())) return Result.success();
        return Uploader.flush(getApplicationContext()) ? Result.success() : Result.retry();
    }

    /** Send what is waiting as soon as the phone is online. */
    public static void schedule(Context context) {
        OneTimeWorkRequest work = new OneTimeWorkRequest.Builder(UploadWorker.class)
            .setConstraints(new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
            .build();
        try {
            WorkManager.getInstance(context.getApplicationContext()).enqueueUniqueWork(NAME, ExistingWorkPolicy.REPLACE, work);
        } catch (IllegalStateException e) {
            // WorkManager not ready (very early after boot): send directly instead
            Uploader.flushAsync(context, null);
        }
    }
}
