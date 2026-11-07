package com.cyph.app

import android.app.AlarmManager
import android.app.PendingIntent
import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.BroadcastReceiver
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.Bundle
import android.os.SystemClock
import android.util.TypedValue
import android.view.View
import android.widget.RemoteViews
import kotlin.random.Random

/** The silly widgets. One rule for all of them: the glowing edge is the readout (progress, fullness, energy), the middle
 *  holds ONE hero (his face, a ball, a number), under it ONE line of text, and the whole widget is the tap target.
 *
 *  A launcher draws a widget from a RemoteViews and nothing else runs in it, so there are three tricks:
 *   1. Taps are broadcasts to the provider itself, so a tap does something without opening the app.
 *   2. Idle motion (blinking, a blinking cursor) is a ViewFlipper, which the launcher animates on its own.
 *   3. A reaction (a boop, a coin spin, the edge sweeping round) is a short burst of partial updates sent from a
 *      background thread while the broadcast is held open with goAsync(); 1 to 7 seconds, then redrawn at rest. */
object Sw {
    const val ACT = "com.cyph.app.SILLY"
    const val GREEN = 0xFF5DFF7A.toInt()
    const val AMBER = 0xFFD9984A.toInt()
    const val CORAL = 0xFFF08A73.toInt()
    const val TEAL = 0xFF2F9D8F.toInt()
    private val FL = intArrayOf(R.id.fl0, R.id.fl1, R.id.fl2, R.id.fl3, R.id.fl4, R.id.fl5, R.id.fl6)

    fun p(ctx: Context) = ctx.getSharedPreferences("cyph_silly", Context.MODE_PRIVATE)

    fun res(ctx: Context, name: String): Int {
        val id = ctx.resources.getIdentifier(name, "drawable", ctx.packageName)
        return if (id != 0) id else R.drawable.face_rest
    }

    fun face(ctx: Context, mood: String, blink: Boolean = false) = res(ctx, "fz_$mood" + if (blink) "_b" else "")

    /** The idle loop: six open-eyed frames and one blink, flipped every 420 ms, so he blinks about every 3 seconds. */
    fun loop(ctx: Context, rv: RemoteViews, mood: String) {
        val open = face(ctx, mood)
        val shut = face(ctx, mood, true)
        FL.forEachIndexed { i, id -> rv.setImageViewResource(id, if (i == FL.lastIndex) shut else open) }
        rv.setViewVisibility(R.id.idle, View.VISIBLE)
        rv.setViewVisibility(R.id.face, View.GONE)
    }

    /** Swaps the loop for one still frame (used by reactions). */
    fun still(ctx: Context, rv: RemoteViews, mood: String, blink: Boolean = false) {
        rv.setViewVisibility(R.id.idle, View.GONE)
        rv.setViewVisibility(R.id.face, View.VISIBLE)
        rv.setImageViewResource(R.id.face, face(ctx, mood, blink))
    }

    fun tap(ctx: Context, cls: Class<*>, what: String): PendingIntent =
        PendingIntent.getBroadcast(
            ctx, (cls.name + what).hashCode(), Intent(ctx, cls).setAction("$ACT:$what"),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

    fun <T> pick(list: List<T>): T = list[Random.nextInt(list.size)]

    /** Called by the 15-minute tick: things that change with the clock (snacks, battery, a nap that ended). */
    fun redrawAll(ctx: Context) {
        for (w in listOf(WidgetSnack(), WidgetBattery(), WidgetNap(), WidgetBoop(), WidgetBall(), WidgetCoin(), WidgetExcuse(), WidgetBusy())) w.redraw(ctx)
    }
}

/** A widget's size in dp, so the edge bitmap fits it exactly. */
class Sz(val w: Int, val h: Int)

abstract class SillyWidget : AppWidgetProvider() {
    abstract val layout: Int
    open val defW = 156
    open val defH = 156
    abstract fun fill(ctx: Context, rv: RemoteViews, sz: Sz)

    /** A tap. Runs on a background thread, so it may sleep between animation frames (keep the total under about 8 s). */
    open fun act(ctx: Context, what: String) {}

    private fun size(mgr: AppWidgetManager, id: Int): Sz {
        val o = mgr.getAppWidgetOptions(id)
        val w = o.getInt(AppWidgetManager.OPTION_APPWIDGET_MIN_WIDTH, 0)
        val h = o.getInt(AppWidgetManager.OPTION_APPWIDGET_MAX_HEIGHT, 0)
        return Sz(if (w > 0) w else defW, if (h > 0) h else defH)
    }

    private fun build(ctx: Context, sz: Sz) = RemoteViews(ctx.packageName, layout).also { fill(ctx, it, sz) }
    private fun ids(ctx: Context): IntArray = AppWidgetManager.getInstance(ctx).getAppWidgetIds(ComponentName(ctx, javaClass))

    fun redraw(ctx: Context) {
        val mgr = AppWidgetManager.getInstance(ctx)
        for (id in ids(ctx)) mgr.updateAppWidget(id, build(ctx, size(mgr, id)))
    }

    /** One animation frame: only the views `f` touches change. */
    protected fun frame(ctx: Context, f: (RemoteViews, Sz) -> Unit) {
        val mgr = AppWidgetManager.getInstance(ctx)
        for (id in ids(ctx)) {
            val sz = size(mgr, id)
            mgr.partiallyUpdateAppWidget(id, RemoteViews(ctx.packageName, layout).also { f(it, sz) })
        }
    }

    protected fun edge(ctx: Context, rv: RemoteViews, sz: Sz, p: Float, color: Int = Sw.GREEN) =
        rv.setImageViewBitmap(R.id.edge, Edge.draw(ctx, sz.w, sz.h, p, color))

    /** The edge sweeping once round, as feedback for a tap. */
    protected fun sweep(ctx: Context, color: Int, steps: Int, ms: Long) {
        for (i in 1..steps) {
            frame(ctx) { rv, sz -> edge(ctx, rv, sz, i / steps.toFloat(), color) }
            pause(ms)
        }
    }

    protected fun pause(ms: Long) = try { Thread.sleep(ms) } catch (_: InterruptedException) {}

    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) {
        for (id in ids) mgr.updateAppWidget(id, build(ctx, size(mgr, id)))
    }

    override fun onAppWidgetOptionsChanged(ctx: Context, mgr: AppWidgetManager, id: Int, newOptions: Bundle) {
        mgr.updateAppWidget(id, build(ctx, size(mgr, id)))
    }

    override fun onReceive(ctx: Context, i: Intent) {
        val a = i.action ?: ""
        if (a.startsWith(Sw.ACT + ":")) {
            val pr = goAsync()
            Thread {
                try { act(ctx, a.substringAfter(':')) } catch (_: Exception) { /* a failed trick must not crash the launcher */ }
                finally { redraw(ctx); pr.finish() }
            }.start()
        } else super.onReceive(ctx, i)
    }
}

/** Boop the snoot. The edge fills towards the next 50 boops. Boop fast and he gets silly, then grumpy. */
class WidgetBoop : SillyWidget() {
    override val layout = R.layout.widget_boop

    override fun fill(ctx: Context, rv: RemoteViews, sz: Sz) {
        val n = Sw.p(ctx).getInt("boops", 0)
        Sw.loop(ctx, rv, "rest")
        rv.setTextViewText(R.id.lbl, "$n BOOPS")
        rv.setTextColor(R.id.lbl, Sw.GREEN)
        rv.setTextViewText(R.id.hint, if (n > 0 && n % 50 == 0) "$n boops. he knows." else "tap his snoot")
        edge(ctx, rv, sz, if (n > 0 && n % 50 == 0) 1f else (n % 50) / 50f)
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "boop"))
    }

    override fun act(ctx: Context, what: String) {
        val now = System.currentTimeMillis()
        val p = Sw.p(ctx)
        val recent = (p.getString("boop_t", "") ?: "").split(',').mapNotNull { it.toLongOrNull() }.filter { now - it < 8000 } + now
        val n = p.getInt("boops", 0) + 1
        p.edit().putInt("boops", n).putString("boop_t", recent.joinToString(",")).apply()
        val (seq, say) = when {
            recent.size >= 7 -> listOf("grumpy", "grumpy", "grumpy_b", "grumpy") to "STOP IT"
            recent.size >= 4 -> listOf("laugh", "laugh_b", "laugh", "laugh_b") to "hehehe"
            else -> listOf("boop", "purr", "purr_b", "purr") to Sw.pick(listOf("*purr*", "boop received", "eep", "again", "nose. ok."))
        }
        val p50 = if (n % 50 == 0) 1f else (n % 50) / 50f
        for (m in seq) {
            frame(ctx) { rv, sz ->
                Sw.still(ctx, rv, m.removeSuffix("_b"), m.endsWith("_b"))
                rv.setTextViewText(R.id.lbl, "$n BOOPS")
                rv.setTextViewText(R.id.hint, say)
                edge(ctx, rv, sz, p50)
            }
            pause(190)
        }
    }
}

/** Feed him. The edge is the bowl: it drains over eight hours and goes green, amber, coral as he gets hungrier. */
class WidgetSnack : SillyWidget() {
    override val layout = R.layout.widget_snack
    private val DRAIN_MS = 8 * 3600_000.0

    private fun level(ctx: Context, now: Long = System.currentTimeMillis()): Int {
        val p = Sw.p(ctx)
        val start = p.getFloat("snack", 60f)
        val at = p.getLong("snack_at", now)
        return (start - (now - at) / DRAIN_MS * 100.0).toInt().coerceIn(0, 100)
    }

    private fun tone(l: Int) = if (l >= 50) Sw.GREEN else if (l >= 25) Sw.AMBER else Sw.CORAL

    override fun fill(ctx: Context, rv: RemoteViews, sz: Sz) {
        val l = level(ctx)
        val (mood, line) = when {
            l >= 85 -> "happy" to "stuffed. no notes."
            l >= 50 -> "rest" to "content, for now"
            l >= 25 -> "curious" to "is that a snack? no?"
            l >= 8 -> "sad" to "my bowl is a void"
            else -> "nervous" to "tell my plants i loved them"
        }
        Sw.loop(ctx, rv, mood)
        rv.setTextViewText(R.id.lbl, "SNACKS $l%")
        rv.setTextColor(R.id.lbl, tone(l))
        rv.setTextViewText(R.id.hint, line)
        edge(ctx, rv, sz, l / 100f, tone(l))
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "feed"))
    }

    override fun act(ctx: Context, what: String) {
        val now = System.currentTimeMillis()
        val cur = level(ctx, now)
        if (cur >= 95) {
            for (m in listOf("sneeze", "laugh", "laugh")) {
                frame(ctx) { rv, _ -> Sw.still(ctx, rv, m); rv.setTextViewText(R.id.hint, "i'm FULL. rude.") }
                pause(260)
            }
            return
        }
        val to = minOf(100, cur + 35)
        Sw.p(ctx).edit().putFloat("snack", to.toFloat()).putLong("snack_at", now).apply()
        val seq = listOf("excited" to "CRUNCH", "happy" to "nom nom", "happy_b" to "nom nom", "happy" to "crunch crunch", "love" to "more.")
        seq.forEachIndexed { i, (m, say) ->
            val shown = cur + (to - cur) * (i + 1) / seq.size
            frame(ctx) { rv, sz ->
                Sw.still(ctx, rv, m.removeSuffix("_b"), m.endsWith("_b"))
                rv.setTextViewText(R.id.lbl, "SNACKS $shown%")
                rv.setTextViewText(R.id.hint, say)
                edge(ctx, rv, sz, shown / 100f, tone(shown))
            }
            pause(260)
        }
    }
}

/** Shake for advice. The edge sweeps round while it shakes. The answers are a local list, so it works offline. */
class WidgetBall : SillyWidget() {
    override val layout = R.layout.widget_ball
    private val answers = listOf(
        "ask again after snacks", "skill issue", "yes, but make it weird", "no. next question.", "the vibes say maybe",
        "do it. what is the worst that could happen (a lot)", "consult a pigeon", "absolutely not, and also yes", "bold of you to assume",
        "your future self sends a sigh", "only if you drink water first", "i am a ball. why are you asking me", "trust the process (i do not)",
        "ears say yes", "tail says no", "touch grass, then decide", "outlook: chaotic", "sleep on it. literally.", "hmm. no. hmmmm. yes.",
        "that is a tomorrow problem", "do the smaller thing first", "very likely, sorry", "i would, but i am a ball", "snacks first, then yes",
        "mercury is in the wrong cable", "unclear. like your handwriting", "yes!! (i did not read the question)", "cannot predict, too fluffy",
        "the answer is in the last place you look", "obviously. why even ask", "nope. my ears went flat", "go for it, i will be here",
    )

    private fun pad(ctx: Context, dp: Int) = (dp * ctx.resources.displayMetrics.density).toInt()

    override fun fill(ctx: Context, rv: RemoteViews, sz: Sz) {
        rv.setTextViewText(R.id.lbl, "TAP TO SHAKE")
        rv.setTextColor(R.id.lbl, Sw.GREEN)
        rv.setViewPadding(R.id.ball, pad(ctx, 2), pad(ctx, 2), pad(ctx, 2), pad(ctx, 2))
        // a true circle: as wide as the room under the label allows (RemoteViews can resize a view from Android 12)
        if (android.os.Build.VERSION.SDK_INT >= 31) {
            val d = minOf(sz.w - 36 - 8, sz.h - 36 - 10 - 8 - 12).coerceAtLeast(60).toFloat()
            rv.setViewLayoutWidth(R.id.ball, d, TypedValue.COMPLEX_UNIT_DIP)
            rv.setViewLayoutHeight(R.id.ball, d, TypedValue.COMPLEX_UNIT_DIP)
        }
        rv.setTextViewText(R.id.ans, Sw.p(ctx).getString("ball", "ask me something") ?: "ask me something")
        edge(ctx, rv, sz, 1f)
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "shake"))
    }

    override fun act(ctx: Context, what: String) {
        for (i in 0 until 12) {
            val lean = if (i % 2 == 0) 0 else 12
            frame(ctx) { rv, sz ->
                rv.setViewPadding(R.id.ball, pad(ctx, lean), pad(ctx, (i % 3) * 3), pad(ctx, 12 - lean), pad(ctx, 0))
                rv.setTextViewText(R.id.ans, "· · ·".substring(0, 1 + (i % 3) * 2))
                edge(ctx, rv, sz, (i + 1) / 12f)
            }
            pause(75)
        }
        val a = Sw.pick(answers)
        Sw.p(ctx).edit().putString("ball", a).apply()
        for (n in intArrayOf(a.length / 3, a.length * 2 / 3)) { // typewriter reveal
            frame(ctx) { rv, _ -> rv.setViewPadding(R.id.ball, pad(ctx, 2), pad(ctx, 2), pad(ctx, 2), pad(ctx, 2)); rv.setTextViewText(R.id.ans, a.take(n)) }
            pause(140)
        }
    }
}

/** Two big halves, 🪙 and 🎲, and a line from him. Tap a half and it spins, slows, and lands. */
class WidgetCoin : SillyWidget() {
    override val layout = R.layout.widget_coin
    private val coinLines = listOf("the coin has spoken. it lies sometimes.", "fate says so (fate is bored)", "best of three? no.", "i flipped it, you decide if you agree")
    private val dice = mapOf(
        1 to "critical fail. iconic.", 2 to "technically a number", 3 to "mid. respect.", 4 to "decent. not great, not terrible",
        5 to "oooh, so close", 6 to "NAT 6. unbearable.",
    )

    private fun show(rv: RemoteViews, coin: Boolean, text: String, big: Boolean) {
        val id = if (coin) R.id.ecoin else R.id.edice
        rv.setTextViewText(id, text)
        rv.setTextViewTextSize(id, TypedValue.COMPLEX_UNIT_SP, if (big) 40f else 20f)
    }

    override fun fill(ctx: Context, rv: RemoteViews, sz: Sz) {
        val p = Sw.p(ctx)
        val last = p.getString("coin_kind", "") ?: ""
        show(rv, true, if (last == "coin") p.getString("coin_res", "🪙") ?: "🪙" else "🪙", last != "coin")
        show(rv, false, if (last == "dice") p.getString("coin_res", "🎲") ?: "🎲" else "🎲", true)
        rv.setTextViewText(R.id.ecoinc, "FLIP")
        rv.setTextViewText(R.id.edicec, "ROLL")
        rv.setTextViewText(R.id.hint, p.getString("coin_sub", "tap one. he will judge you.") ?: "")
        edge(ctx, rv, sz, 1f)
        rv.setOnClickPendingIntent(R.id.zcoin, Sw.tap(ctx, javaClass, "coin"))
        rv.setOnClickPendingIntent(R.id.zdice, Sw.tap(ctx, javaClass, "dice"))
    }

    override fun act(ctx: Context, what: String) {
        val coin = what == "coin"
        val faces = if (coin) listOf("HEADS", "TAILS") else listOf("1", "2", "3", "4", "5", "6")
        val finalRes = Sw.pick(faces)
        for (i in 0 until 10) {
            val shown = if (i == 9) finalRes else if (coin) faces[i % 2] else Sw.pick(faces)
            frame(ctx) { rv, sz -> show(rv, coin, shown, !coin); rv.setTextViewText(R.id.hint, "..."); edge(ctx, rv, sz, (i + 1) / 10f) }
            pause(55L + i * i * 6L)
        }
        val say = if (coin) Sw.pick(coinLines) else dice.getValue(finalRes.toInt())
        Sw.p(ctx).edit().putString("coin_kind", if (coin) "coin" else "dice").putString("coin_res", finalRes).putString("coin_sub", say).apply()
    }
}

/** Tap the number to change the minutes, tap START to nap. The edge fills as the nap goes on. */
class WidgetNap : SillyWidget() {
    override val layout = R.layout.widget_nap
    private val choices = intArrayOf(10, 20, 45)

    override fun fill(ctx: Context, rv: RemoteViews, sz: Sz) {
        val p = Sw.p(ctx)
        val end = p.getLong("nap_end", 0)
        val len = p.getLong("nap_len", 20 * 60_000L)
        val left = end - System.currentTimeMillis()
        val napping = left > 0
        Sw.loop(ctx, rv, if (napping) "sleepy" else "rest")
        rv.setTextViewText(R.id.lbl, if (napping) "NAPPING · TAP TO WAKE" else "NAP TIMER")
        rv.setTextColor(R.id.lbl, Sw.GREEN)
        rv.setViewVisibility(R.id.idlegrp, if (napping) View.GONE else View.VISIBLE)
        rv.setViewVisibility(R.id.napgrp, if (napping) View.VISIBLE else View.GONE)
        rv.setTextViewText(R.id.num, (p.getInt("nap_pick", 20)).toString())
        rv.setTextViewText(R.id.go, "START NAP")
        rv.setViewVisibility(R.id.go, if (napping) View.GONE else View.VISIBLE)
        if (napping) {
            rv.setChronometerCountDown(R.id.chrono, true)
            rv.setChronometer(R.id.chrono, SystemClock.elapsedRealtime() + left, null, true)
            edge(ctx, rv, sz, 1f - left.toFloat() / len)
            rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "wake"))
            tickLater(ctx)
        } else {
            edge(ctx, rv, sz, 0f)
            rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "pick"))
            rv.setOnClickPendingIntent(R.id.idlegrp, Sw.tap(ctx, javaClass, "pick"))
            rv.setOnClickPendingIntent(R.id.go, Sw.tap(ctx, javaClass, "go"))
        }
    }

    private fun alarm(ctx: Context) = PendingIntent.getBroadcast(
        ctx, 7101, Intent(ctx, NapDone::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    /** The Chronometer ticks by itself; the edge arc is a picture, so redraw it every 30 seconds while napping. */
    private fun tickLater(ctx: Context) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        val pi = PendingIntent.getBroadcast(ctx, 7102, Intent(ctx, NapTick::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        am.setAndAllowWhileIdle(AlarmManager.RTC, System.currentTimeMillis() + 30_000L, pi)
    }

    override fun act(ctx: Context, what: String) {
        val p = Sw.p(ctx)
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        when (what) {
            "pick" -> {
                val next = choices[(choices.indexOf(p.getInt("nap_pick", 20)) + 1) % choices.size]
                p.edit().putInt("nap_pick", next).apply()
            }
            "wake" -> {
                am.cancel(alarm(ctx))
                p.edit().putLong("nap_end", 0).apply()
            }
            "go" -> {
                val len = p.getInt("nap_pick", 20) * 60_000L
                val end = System.currentTimeMillis() + len
                p.edit().putLong("nap_end", end).putLong("nap_len", len).apply()
                am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, end, alarm(ctx))
                for (m in listOf("yawn", "sleepy")) {
                    frame(ctx) { rv, _ -> rv.setViewVisibility(R.id.idlegrp, View.GONE); rv.setViewVisibility(R.id.napgrp, View.VISIBLE); Sw.still(ctx, rv, m) }
                    pause(450)
                }
            }
        }
    }
}

class NapTick : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        if (Sw.p(ctx).getLong("nap_end", 0) > System.currentTimeMillis()) WidgetNap().redraw(ctx)
    }
}

/** The nap ended: he stretches, and says so (a notification, in case the phone is face down). */
class NapDone : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        Sw.p(ctx).edit().putLong("nap_end", 0).apply()
        Notifier.show(ctx, "reminder", "Nap over", "wake up, nerd. he is already stretching.", "happy", "nap:done")
        val pr = goAsync()
        Thread {
            try {
                val mgr = AppWidgetManager.getInstance(ctx)
                val ids = mgr.getAppWidgetIds(ComponentName(ctx, WidgetNap::class.java))
                for (m in listOf("yawn", "stretch", "happy")) {
                    if (ids.isNotEmpty()) mgr.partiallyUpdateAppWidget(ids, RemoteViews(ctx.packageName, R.layout.widget_nap).also {
                        it.setViewVisibility(R.id.idlegrp, View.GONE); it.setViewVisibility(R.id.napgrp, View.VISIBLE); Sw.still(ctx, it, m)
                    })
                    Thread.sleep(500)
                }
                WidgetNap().redraw(ctx)
            } catch (_: Exception) {
            } finally { pr.finish() }
        }.start()
    }
}

/** "I don't wanna": a ridiculous excuse for whatever block is on now. The edge sweeps while it spins, then it settles. */
class WidgetExcuse : SillyWidget() {
    override val layout = R.layout.widget_excuse
    override val defW = 320
    private val who = listOf(
        "Mercury", "my left ear", "the wifi", "a very specific pigeon", "the vibes", "my other 47 tabs", "gravity", "the moon", "my cat's lawyer",
        "Tuesday", "the printer", "my ancestors", "the tail situation", "the cloud", "a stern raccoon", "my chair", "the arm",
    )
    private val did = listOf(
        "is in retrograde", "is being weird about it", "filed a complaint", "said no", "needs a nap first", "is still buffering",
        "has not been briefed", "ate my motivation", "is on strike", "wants a snack", "is not ready for this conversation",
        "went to get milk", "is doing its best", "sent a strongly worded email",
    )

    private fun task(ctx: Context): String {
        val v = Widgets.view(ctx)
        return if (v.label.startsWith("●") && v.label.contains("NOW")) v.title.lowercase() else "do that"
    }

    private fun roll(ctx: Context) = "I can't ${task(ctx)} because ${Sw.pick(who)} ${Sw.pick(did)}."

    override fun fill(ctx: Context, rv: RemoteViews, sz: Sz) {
        rv.setTextViewText(R.id.lbl, "EXCUSE GENERATOR")
        rv.setTextColor(R.id.lbl, Sw.AMBER)
        rv.setTextViewText(R.id.excuse, Sw.p(ctx).getString("excuse", null) ?: "Avoiding something? Tap. I will cover for you.")
        rv.setImageViewResource(R.id.face, Sw.face(ctx, "smug"))
        edge(ctx, rv, sz, 1f, Sw.AMBER)
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "roll"))
    }

    override fun act(ctx: Context, what: String) {
        var last = ""
        for (i in 0 until 9) {
            last = roll(ctx)
            val mood = if (i % 2 == 0) "smug" else "laugh"
            frame(ctx) { rv, sz -> rv.setTextViewText(R.id.excuse, last); rv.setImageViewResource(R.id.face, Sw.face(ctx, mood)); edge(ctx, rv, sz, (i + 1) / 9f, Sw.AMBER) }
            pause(70L + i * 35)
        }
        Sw.p(ctx).edit().putString("excuse", last).apply()
    }
}

/** Look busy: fake build logs scroll for a few seconds and the edge is the build progress. */
class WidgetBusy : SillyWidget() {
    override val layout = R.layout.widget_busy
    override val defW = 320
    private val ids = intArrayOf(R.id.l0, R.id.l1, R.id.l2, R.id.l3, R.id.l4)
    private val fake = listOf(
        "compiling vibes.rs", "linking feelings (3 warnings)", "resolving dependencies on snacks", "running 412 tests (409 are just vibes)",
        "optimising the ears", "refactoring the refactor", "fetching more RAM", "deploying to production (it is a Tuesday)",
        "reticulating splines", "rebasing onto the weekend", "indexing 14 GB of tabs", "garbage collecting regrets",
        "calibrating the arm (it is fine)", "waiting on code review (from a pigeon)", "pushing to main, confidently",
    )

    private fun stamp(): String = "%02d:%02d:%02d".format(Random.nextInt(24), Random.nextInt(60), Random.nextInt(60))
    private fun line() = "${stamp()} ${Sw.pick(fake)}"

    private fun lines(ctx: Context): List<String> =
        (Sw.p(ctx).getString("busy_log", "") ?: "").split('\n').filter { it.isNotBlank() }.takeLast(5)

    private fun show(rv: RemoteViews, log: List<String>) {
        val shown = List(5 - log.size) { "" } + log
        ids.forEachIndexed { i, id -> rv.setTextViewText(id, shown[i]) }
    }

    override fun fill(ctx: Context, rv: RemoteViews, sz: Sz) {
        show(rv, lines(ctx).ifEmpty { listOf("$ tap to look busy", "$ nobody will ask") })
        rv.setTextViewText(R.id.lbl, "LOOK BUSY · TAP")
        rv.setTextColor(R.id.lbl, Sw.TEAL)
        edge(ctx, rv, sz, 1f, Sw.TEAL)
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "busy"))
    }

    override fun act(ctx: Context, what: String) {
        var log = lines(ctx)
        val steps = 16
        for (i in 0 until steps) {
            log = (log + line()).takeLast(5)
            frame(ctx) { rv, sz -> show(rv, log); rv.setTextViewText(R.id.lbl, "● BUILDING · ${(i + 1) * 100 / steps}%"); edge(ctx, rv, sz, (i + 1) / steps.toFloat(), Sw.TEAL) }
            pause(380)
        }
        log = (log + "${stamp()} BUILD SUCCESSFUL (nobody checked)").takeLast(5)
        Sw.p(ctx).edit().putString("busy_log", log.joinToString("\n")).apply()
        frame(ctx) { rv, sz -> show(rv, log); rv.setTextViewText(R.id.lbl, "✔ DONE · TAP TO AGAIN"); edge(ctx, rv, sz, 1f, Sw.TEAL) }
        pause(400)
    }
}

/** Your phone's battery as his feelings, and the edge is the battery. No link to home needed. */
class WidgetBattery : SillyWidget() {
    override val layout = R.layout.widget_battery
    override val defW = 156
    override val defH = 64

    private fun lines(level: Int, charging: Boolean): Pair<String, String> {
        if (charging) return "excited" to Sw.pick(listOf("nom nom electricity", "plugged in. i see colours", "charging, do not disturb"))
        return when {
            level >= 90 -> "excited" to Sw.pick(listOf("FULL POWER. i could fight a cloud", "hyper. send help"))
            level >= 60 -> "happy" to Sw.pick(listOf("vibing, battery-wise", "plenty left. be bold"))
            level >= 30 -> "rest" to Sw.pick(listOf("it is fine. it is fine.", "i feel the number going down"))
            level >= 15 -> "nervous" to Sw.pick(listOf("please find a charger", "i am getting dramatic"))
            else -> "sad" to Sw.pick(listOf("tell my plants i loved them", "going dark. avenge me", "this is how it ends"))
        }
    }

    override fun fill(ctx: Context, rv: RemoteViews, sz: Sz) {
        val b = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = b?.let { it.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) * 100 / maxOf(1, it.getIntExtra(BatteryManager.EXTRA_SCALE, 100)) } ?: 50
        val charging = (b?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        val (mood, line) = lines(level, charging)
        val tone = if (charging || level >= 50) Sw.GREEN else if (level >= 20) Sw.AMBER else Sw.CORAL
        Sw.loop(ctx, rv, mood)
        rv.setTextViewText(R.id.lbl, (if (charging) "⚡ " else "") + "$level%")
        rv.setTextViewText(R.id.title, line)
        edge(ctx, rv, sz, level / 100f, tone)
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "poke"))
    }

    override fun act(ctx: Context, what: String) {
        for (m in listOf("startle", "confused")) {
            frame(ctx) { rv, _ -> Sw.still(ctx, rv, m) }
            pause(350)
        }
    }
}
