package com.expensetracker.app.autotrack;

import java.util.Locale;
import java.util.regex.Pattern;

/**
 * Decides on the phone which messages may be about money, so personal messages and one-time passwords never leave it.
 * The server reads the message properly; this only keeps everything else private.
 */
public final class BankFilter {
    private static final Pattern PHONE_NUMBER = Pattern.compile("^\\+?[\\d\\s-]{6,}$");
    private static final Pattern AMOUNT = Pattern.compile("(?i)(rs\\.?|inr|₹)\\s*[\\d,]+");
    private static final Pattern MONEY_WORD = Pattern.compile(
        "(?i)\\b(debited|credited|spent|withdrawn|paid|sent|received|deposited|refund|reversed|purchase|txn|transaction|"
            + "deducted|charged|due|statement|transferred|used\\s+(for|at)|payments?|paying|sent\\s+you)\\b");
    private static final Pattern SECRET = Pattern.compile(
        "(?i)\\b(otp|one[\\s-]?time\\s+password|verification\\s+code|passcode|password|cvv|mpin|pin\\s+is)\\b");

    // Apps whose notifications are about money (bank, card and payment apps); chats and email are never read
    private static final String[] FINANCE_APP_WORDS = {
        "bank", "pay", "upi", "card", "hdfc", "icici", "axis", "sbi", "kotak", "hsbc", "scapia", "onecard", "cred",
        "yesbank", "idfc", "indus", "rbl", "federal", "citi", "amex", "jupiter", "slice", "fampay", "mobikwik",
        "freecharge", "bhim", "phonepe", "paisa", "fi.money", "wallet", "fastag", "finance", "money",
    };
    private static final String[] NEVER_APPS = {
        "com.whatsapp", "org.telegram", "com.instagram", "com.facebook", "com.google.android.gm", "com.snapchat",
        "com.discord", "com.microsoft.teams", "com.slack", "com.truecaller", "com.linkedin", "com.twitter",
    };
    // Messaging apps repeat the SMS we already read
    private static final String[] SMS_APPS = {
        "com.google.android.apps.messaging", "com.samsung.android.messaging", "com.android.mms", "com.oneplus.mms",
        "com.miui.mms", "com.sonyericsson.conversations", "com.motorola.messaging",
    };

    private BankFilter() {}

    /** A business sender (AD-HDFCBK), not a person's phone number. */
    public static boolean isBusinessSender(String sender) {
        return sender != null && !sender.trim().isEmpty() && !PHONE_NUMBER.matcher(sender.trim()).matches();
    }

    public static boolean isSecret(String text) {
        return text != null && SECRET.matcher(text).find();
    }

    /** Has an amount and a word about money moving or a bill, and isn't a one-time password. */
    public static boolean isMoneyText(String text) {
        return text != null && AMOUNT.matcher(text).find() && MONEY_WORD.matcher(text).find() && !isSecret(text);
    }

    public static boolean isBankSms(String sender, String body) {
        return isBusinessSender(sender) && isMoneyText(body);
    }

    public static boolean isSmsApp(String pkg) {
        for (String p : SMS_APPS) if (pkg.startsWith(p)) return true;
        return false;
    }

    public static boolean isFinanceApp(String pkg) {
        String p = pkg.toLowerCase(Locale.ROOT);
        for (String never : NEVER_APPS) if (p.startsWith(never)) return false;
        if (isSmsApp(p)) return false;
        for (String w : FINANCE_APP_WORDS) if (p.contains(w)) return true;
        return false;
    }
}
