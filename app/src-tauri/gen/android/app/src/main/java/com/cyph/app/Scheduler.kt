package com.cyph.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import org.json.JSONArray
import org.json.JSONObject

/** Reminders are alarms set ahead of time, so they ring even when the app (and its webview) is asleep or killed. The app
 *  sends the list whenever the plan changes; it replaces the old list. The list is also kept so a reboot can set it again. */
object Scheduler {
    private const val PREFS = "cyph_native"
    const val FIRE = "com.cyph.app.FIRE"

    private fun prefs(ctx: Context) = ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    private fun pending(ctx: Context, key: String, extras: Intent.() -> Unit = {}): PendingIntent =
        PendingIntent.getBroadcast(
            ctx, key.hashCode(), Intent(ctx, AlarmReceiver::class.java).setAction(FIRE).putExtra("key", key).apply(extras),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

    private fun cancelAll(ctx: Context, am: AlarmManager) {
        val old = JSONArray(prefs(ctx).getString("sched", "[]"))
        for (i in 0 until old.length()) am.cancel(pending(ctx, old.getJSONObject(i).getString("key")))
    }

    fun set(ctx: Context, list: JSONArray) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        cancelAll(ctx, am)
        val now = System.currentTimeMillis()
        val keep = JSONArray()
        for (i in 0 until list.length()) {
            val o = list.getJSONObject(i)
            if (o.getLong("at") <= now) continue
            keep.put(o)
            arm(ctx, am, o)
        }
        prefs(ctx).edit().putString("sched", keep.toString()).apply()
    }

    fun restore(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val saved = JSONArray(prefs(ctx).getString("sched", "[]"))
        val now = System.currentTimeMillis()
        for (i in 0 until saved.length()) if (saved.getJSONObject(i).getLong("at") > now) arm(ctx, am, saved.getJSONObject(i))
    }

    fun later(ctx: Context, o: JSONObject) = arm(ctx, ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager, o)

    private fun arm(ctx: Context, am: AlarmManager, o: JSONObject) {
        val key = o.getString("key")
        val pi = pending(ctx, key) {
            putExtra("kind", o.optString("kind", "reminder")).putExtra("title", o.optString("title"))
                .putExtra("text", o.optString("text")).putExtra("mood", o.optString("mood", "rest")).putExtra("at", o.getLong("at"))
        }
        val at = o.getLong("at")
        try {
            am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi)
        } catch (_: SecurityException) {
            am.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, at, pi) // exact alarms not allowed: a few minutes' slack
        }
    }
}

class AlarmReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        if (i.action != Scheduler.FIRE) return
        Notifier.show(
            ctx, i.getStringExtra("kind") ?: "reminder", i.getStringExtra("title") ?: "Cyph", i.getStringExtra("text") ?: "",
            i.getStringExtra("mood") ?: "rest", i.getStringExtra("key") ?: "k", i.getLongExtra("at", System.currentTimeMillis()),
        )
        Widgets.updateAll(ctx)
    }
}

/** SNOOZE 10 on a notification: ring again in ten minutes. */
class ActionReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        if (i.action != SNOOZE) return
        val key = (i.getStringExtra("key") ?: "k") + "+snooze"
        Scheduler.later(
            ctx,
            JSONObject().put("key", key).put("kind", i.getStringExtra("kind") ?: "reminder").put("title", i.getStringExtra("title") ?: "Cyph")
                .put("text", i.getStringExtra("text") ?: "").put("mood", i.getStringExtra("mood") ?: "rest").put("at", System.currentTimeMillis() + 10 * 60_000L),
        )
        androidx.core.app.NotificationManagerCompat.from(ctx).cancel((i.getStringExtra("key") ?: "k").hashCode())
    }

    companion object {
        const val SNOOZE = "com.cyph.app.SNOOZE"
    }
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        if (i.action == Intent.ACTION_BOOT_COMPLETED || i.action == Intent.ACTION_MY_PACKAGE_REPLACED) {
            Scheduler.restore(ctx)
            Widgets.updateAll(ctx)
            ListenService.start(ctx)
        }
    }
}
