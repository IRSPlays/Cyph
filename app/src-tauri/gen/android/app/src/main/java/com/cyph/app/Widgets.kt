package com.cyph.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.os.SystemClock
import android.view.View
import android.widget.RemoteViews
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale

/** What a widget shows, worked out from the last snapshot the app sent and from the clock at the moment of drawing, so
 *  the minutes left stay right even when the app is not running. */
class WidgetView(
    val bg: Int, val accent: Int, val label: String, val title: String, val sub: String, val pct: Int, val next: String,
    val big: String, val mood: String, val endMs: Long = 0,
)

object Widgets {
    private const val NEUTRAL = 0xFF8A9A78.toInt()
    private val GREEN = 0xFF5DFF7A.toInt()
    private val AMBER = 0xFFD9984A.toInt()
    private val TEAL = 0xFF2F9D8F.toInt()
    private val STALE_MS = 12 * 3600_000L
    private val hm = SimpleDateFormat("HH:mm", Locale.US)

    private fun snap(ctx: Context): JSONObject? =
        try { JSONObject(ctx.getSharedPreferences("cyph_native", Context.MODE_PRIVATE).getString("snap", "") ?: "") } catch (_: Exception) { null }

    fun view(ctx: Context, now: Long = System.currentTimeMillis()): WidgetView {
        val s = snap(ctx)
        val hour = Calendar.getInstance().apply { timeInMillis = now }.get(Calendar.HOUR_OF_DAY)
        val night = hour >= 23 || hour < 6
        if (s == null || now - s.optLong("t") > STALE_MS) {
            return WidgetView(R.drawable.wd_bg_o, NEUTRAL, "NO LINK", "Open Cyph", if (s == null) "Not set up yet." else "Last update was a while ago.", 0, "", "--:--", "sleepy")
        }
        val ev = s.optJSONArray("events")
        var cur: JSONObject? = null
        var nxt: JSONObject? = null
        if (ev != null) for (i in 0 until ev.length()) {
            val e = ev.getJSONObject(i)
            if (e.optBoolean("done")) continue
            if (e.getLong("start") <= now && now < e.getLong("end")) { if (cur == null) cur = e }
            else if (e.getLong("start") > now && nxt == null) nxt = e
        }
        val mood = if (night) "sleepy" else s.optString("mood", "rest").ifBlank { "rest" }
        val arm = s.optJSONObject("arm")
        if (arm != null && arm.optString("state") == "running") {
            return WidgetView(R.drawable.wd_bg_t, TEAL, "ARM · " + arm.optString("step", "working").uppercase(), arm.optString("goal", "Arm task"), "tap to watch", 0, "", "", "rest")
        }
        val nextLine = if (nxt != null) "NEXT " + hm.format(Date(nxt.getLong("start"))) + "  " + nxt.optString("title").uppercase() else ""
        if (!s.optBoolean("connected", true)) {
            return WidgetView(R.drawable.wd_bg_o, NEUTRAL, "NO LINK · " + hm.format(Date(s.optLong("t"))), cur?.optString("title") ?: "Can't reach home", "showing the plan from " + hm.format(Date(s.optLong("t"))), 0, nextLine, "", "sleepy")
        }
        if (cur != null) {
            val st = cur.getLong("start"); val en = cur.getLong("end")
            val pct = (((now - st) * 100) / maxOf(1L, en - st)).toInt().coerceIn(0, 100)
            val left = ((en - now) / 60_000L).toInt()
            return WidgetView(R.drawable.wd_bg_g, GREEN, "● NOW · $pct%", cur.optString("title"), hm.format(Date(st)) + " → " + hm.format(Date(en)) + " · " + fmtLeft(left) + " left", pct, nextLine, fmtLeft(left), mood, en)
        }
        if (nxt != null) {
            val st = nxt.getLong("start")
            val inMin = ((st - now) / 60_000L).toInt()
            return WidgetView(R.drawable.wd_bg_a, AMBER, "NEXT · IN " + fmtLeft(inMin).uppercase(), nxt.optString("title"), "at " + hm.format(Date(st)), 0, "", hm.format(Date(st)), mood)
        }
        return WidgetView(R.drawable.wd_bg_g, GREEN, "● TODAY", "Nothing more today", "enjoy it", 0, "", "--:--", if (night) "sleepy" else "happy")
    }

    private fun fmtLeft(min: Int): String = if (min >= 60) "${min / 60}h ${min % 60}m" else if (min < 1) "<1m" else "${min}m"

    fun updateAll(ctx: Context) {
        val mgr = AppWidgetManager.getInstance(ctx)
        val v = view(ctx)
        for ((cls, kind) in listOf(Widget4x2::class.java to 0, Widget2x2::class.java to 1, WidgetFace::class.java to 2, Widget2x1::class.java to 3)) {
            for (id in mgr.getAppWidgetIds(ComponentName(ctx, cls))) mgr.updateAppWidget(id, build(ctx, kind, v))
        }
        Sw.redrawAll(ctx)
        scheduleTick(ctx)
    }

    private fun build(ctx: Context, kind: Int, v: WidgetView): RemoteViews {
        val rv = RemoteViews(ctx.packageName, when (kind) { 0 -> R.layout.widget_4x2; 1 -> R.layout.widget_2x2; 3 -> R.layout.widget_2x1; else -> R.layout.widget_face })
        rv.setInt(R.id.root, "setBackgroundResource", v.bg)
        if (kind == 2) Sw.loop(ctx, rv, v.mood) else rv.setImageViewResource(R.id.face, Notifier.face(ctx, v.mood))
        rv.setTextViewText(R.id.lbl, if (kind == 2) (if (v.label.startsWith("●")) "● HERE" else v.label.substringBefore(" ·")) else v.label)
        rv.setTextColor(R.id.lbl, v.accent)
        if (kind != 2) rv.setTextViewText(R.id.title, v.title)
        if (kind != 2) rv.setTextViewText(R.id.sub, v.sub)
        if (kind == 0) {
            rv.setProgressBar(R.id.bar, 100, v.pct, false)
            rv.setViewVisibility(R.id.bar, if (v.pct > 0 || v.label.startsWith("●")) View.VISIBLE else View.INVISIBLE)
            rv.setTextViewText(R.id.nxt, v.next)
            // a live countdown that ticks inside the launcher, with no redraw
            val live = v.endMs > System.currentTimeMillis() && v.label.startsWith("●")
            rv.setViewVisibility(R.id.live, if (live) View.VISIBLE else View.GONE)
            if (live) {
                rv.setChronometerCountDown(R.id.live, true)
                rv.setChronometer(R.id.live, SystemClock.elapsedRealtime() + (v.endMs - System.currentTimeMillis()), null, true)
            }
        }
        if (kind == 1) rv.setTextViewText(R.id.big, v.big)
        rv.setOnClickPendingIntent(R.id.root, Notifier.open(ctx))
        return rv
    }

    /** One more redraw at the next moment the picture changes (a block starting or ending), and at least every 15 minutes. */
    fun scheduleTick(ctx: Context) {
        val s = snap(ctx) ?: return
        val now = System.currentTimeMillis()
        var at = now + 15 * 60_000L
        val ev = s.optJSONArray("events")
        if (ev != null) for (i in 0 until ev.length()) {
            val e = ev.getJSONObject(i)
            for (t in listOf(e.getLong("start"), e.getLong("end"))) if (t > now + 1000 && t < at) at = t
        }
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val pi = PendingIntent.getBroadcast(ctx, 7001, Intent(ctx, TickReceiver::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        am.setAndAllowWhileIdle(AlarmManager.RTC, at + 500, pi)
    }
}

open class BaseWidget : AppWidgetProvider() {
    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) = Widgets.updateAll(ctx)
    override fun onEnabled(ctx: Context) = Widgets.updateAll(ctx)
}

class Widget4x2 : BaseWidget()
class Widget2x2 : BaseWidget()
class WidgetFace : BaseWidget()
class Widget2x1 : BaseWidget()

class TickReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) = Widgets.updateAll(ctx)
}
