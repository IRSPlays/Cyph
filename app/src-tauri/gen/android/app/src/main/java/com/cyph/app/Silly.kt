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
import android.os.SystemClock
import android.view.View
import android.widget.RemoteViews
import kotlin.random.Random

/** The silly widgets. A launcher draws a widget from a RemoteViews and nothing else runs in it, so there are exactly
 *  three tricks here:
 *   1. Taps are broadcasts to the provider itself, so a tap does something without opening the app.
 *   2. Idle motion (blinking, a blinking cursor, "zZz") is a ViewFlipper, which the launcher animates on its own.
 *   3. A reaction (a boop, a coin spin) is a short burst of partial updates sent from a background thread while the
 *      broadcast is held open with goAsync(); about 1 to 7 seconds, then the widget is redrawn at rest. */
object Sw {
    const val ACT = "com.cyph.app.SILLY"
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

abstract class SillyWidget : AppWidgetProvider() {
    abstract val layout: Int
    abstract fun fill(ctx: Context, rv: RemoteViews)

    /** A tap. Runs on a background thread, so it may sleep between animation frames (keep the total under about 8 s). */
    open fun act(ctx: Context, what: String) {}

    private fun build(ctx: Context) = RemoteViews(ctx.packageName, layout).also { fill(ctx, it) }
    private fun ids(ctx: Context): IntArray = AppWidgetManager.getInstance(ctx).getAppWidgetIds(ComponentName(ctx, javaClass))

    fun redraw(ctx: Context) {
        val i = ids(ctx)
        if (i.isNotEmpty()) AppWidgetManager.getInstance(ctx).updateAppWidget(i, build(ctx))
    }

    /** One animation frame: only the views `f` touches change. */
    protected fun frame(ctx: Context, f: (RemoteViews) -> Unit) {
        val i = ids(ctx)
        if (i.isNotEmpty()) AppWidgetManager.getInstance(ctx).partiallyUpdateAppWidget(i, RemoteViews(ctx.packageName, layout).also(f))
    }

    protected fun pause(ms: Long) = try { Thread.sleep(ms) } catch (_: InterruptedException) {}

    override fun onUpdate(ctx: Context, mgr: AppWidgetManager, ids: IntArray) {
        mgr.updateAppWidget(ids, build(ctx))
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

/** Boop the snoot. Boop fast and he gets silly, then grumpy. */
class WidgetBoop : SillyWidget() {
    override val layout = R.layout.widget_boop

    override fun fill(ctx: Context, rv: RemoteViews) {
        val n = Sw.p(ctx).getInt("boops", 0)
        Sw.loop(ctx, rv, "rest")
        rv.setTextViewText(R.id.lbl, "BOOPS · $n")
        rv.setTextColor(R.id.lbl, 0xFF5DFF7A.toInt())
        rv.setTextViewText(R.id.hint, if (n > 0 && n % 50 == 0) "$n boops. he knows." else "tap his snoot")
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
        for (m in seq) {
            frame(ctx) { rv -> Sw.still(ctx, rv, m.removeSuffix("_b"), m.endsWith("_b")); rv.setTextViewText(R.id.lbl, "BOOPS · $n"); rv.setTextViewText(R.id.hint, say) }
            pause(190)
        }
    }
}

/** Feed him. Fullness drains over eight hours; at zero he is dramatic about it. */
class WidgetSnack : SillyWidget() {
    override val layout = R.layout.widget_snack
    private val DRAIN_MS = 8 * 3600_000.0

    private fun level(ctx: Context, now: Long = System.currentTimeMillis()): Int {
        val p = Sw.p(ctx)
        val start = p.getFloat("snack", 60f)
        val at = p.getLong("snack_at", now)
        return (start - (now - at) / DRAIN_MS * 100.0).toInt().coerceIn(0, 100)
    }

    override fun fill(ctx: Context, rv: RemoteViews) {
        val l = level(ctx)
        val (mood, line) = when {
            l >= 85 -> "happy" to "stuffed. no notes."
            l >= 50 -> "rest" to "content, for now"
            l >= 25 -> "curious" to "is that a snack? no?"
            l >= 8 -> "sad" to "my bowl is a void"
            else -> "nervous" to "tell my plants i loved them"
        }
        Sw.loop(ctx, rv, mood)
        rv.setTextViewText(R.id.lbl, "SNACK BOWL · $l%")
        rv.setTextColor(R.id.lbl, 0xFFD9984A.toInt())
        rv.setProgressBar(R.id.bar, 100, l, false)
        rv.setTextViewText(R.id.hint, line)
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "feed"))
    }

    override fun act(ctx: Context, what: String) {
        val now = System.currentTimeMillis()
        val cur = level(ctx, now)
        if (cur >= 95) {
            for (m in listOf("sneeze", "laugh", "laugh")) {
                frame(ctx) { rv -> Sw.still(ctx, rv, m); rv.setTextViewText(R.id.hint, "i'm FULL. rude.") }
                pause(260)
            }
            return
        }
        val to = minOf(100, cur + 35)
        Sw.p(ctx).edit().putFloat("snack", to.toFloat()).putLong("snack_at", now).apply()
        val seq = listOf("excited" to "CRUNCH", "happy" to "nom nom", "happy_b" to "nom nom", "happy" to "crunch crunch", "love" to "more.")
        seq.forEachIndexed { i, (m, say) ->
            frame(ctx) { rv ->
                Sw.still(ctx, rv, m.removeSuffix("_b"), m.endsWith("_b"))
                rv.setProgressBar(R.id.bar, 100, cur + (to - cur) * (i + 1) / seq.size, false)
                rv.setTextViewText(R.id.hint, say)
            }
            pause(260)
        }
    }
}

/** Shake for advice. The answers are a local list, so it works with no link to home. */
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

    override fun fill(ctx: Context, rv: RemoteViews) {
        rv.setTextViewText(R.id.lbl, "CYPH-BALL · TAP TO SHAKE")
        rv.setTextColor(R.id.lbl, 0xFF5DFF7A.toInt())
        rv.setViewPadding(R.id.ball, pad(ctx, 4), pad(ctx, 4), pad(ctx, 4), pad(ctx, 4))
        rv.setTextViewText(R.id.ans, Sw.p(ctx).getString("ball", "ask me something") ?: "ask me something")
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "shake"))
    }

    override fun act(ctx: Context, what: String) {
        for (i in 0 until 12) {
            val lean = if (i % 2 == 0) 0 else 14
            frame(ctx) { rv ->
                rv.setViewPadding(R.id.ball, pad(ctx, 2 + lean), pad(ctx, 4 + (i % 3) * 2), pad(ctx, 16 - lean), pad(ctx, 4))
                rv.setTextViewText(R.id.ans, "· · ·".substring(0, 1 + (i % 3) * 2))
            }
            pause(75)
        }
        val a = Sw.pick(answers)
        Sw.p(ctx).edit().putString("ball", a).apply()
        // typewriter reveal
        for (n in intArrayOf(a.length / 3, a.length * 2 / 3)) {
            frame(ctx) { rv -> rv.setViewPadding(R.id.ball, pad(ctx, 4), pad(ctx, 4), pad(ctx, 4), pad(ctx, 4)); rv.setTextViewText(R.id.ans, a.take(n)) }
            pause(140)
        }
    }
}

/** "I don't wanna": a ridiculous excuse for whatever block is on now. A slot machine, then it settles. */
class WidgetExcuse : SillyWidget() {
    override val layout = R.layout.widget_excuse
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

    override fun fill(ctx: Context, rv: RemoteViews) {
        rv.setTextViewText(R.id.lbl, "EXCUSE GENERATOR")
        rv.setTextColor(R.id.lbl, 0xFFD9984A.toInt())
        rv.setTextViewText(R.id.excuse, Sw.p(ctx).getString("excuse", null) ?: "Avoiding something? Tap. I will cover for you.")
        rv.setImageViewResource(R.id.face, Sw.face(ctx, "smug"))
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "roll"))
    }

    override fun act(ctx: Context, what: String) {
        var last = ""
        for (i in 0 until 9) {
            last = roll(ctx)
            val mood = if (i % 2 == 0) "smug" else "laugh"
            frame(ctx) { rv -> rv.setTextViewText(R.id.excuse, last); rv.setImageViewResource(R.id.face, Sw.face(ctx, mood)) }
            pause(70L + i * 35)
        }
        Sw.p(ctx).edit().putString("excuse", last).apply()
        frame(ctx) { rv -> rv.setImageViewResource(R.id.face, Sw.face(ctx, "smug")) }
    }
}

/** Look busy: fake build logs scroll for a few seconds, with a blinking prompt (a ViewFlipper) when idle. */
class WidgetBusy : SillyWidget() {
    override val layout = R.layout.widget_busy
    private val ids = intArrayOf(R.id.l0, R.id.l1, R.id.l2, R.id.l3, R.id.l4, R.id.l5)
    private val fake = listOf(
        "compiling vibes.rs", "linking feelings (3 warnings)", "resolving dependencies on snacks", "running 412 tests (409 are just vibes)",
        "optimising the ears", "refactoring the refactor", "fetching more RAM", "deploying to production (it is a Tuesday)",
        "reticulating splines", "rebasing onto the weekend", "indexing 14 GB of tabs", "garbage collecting regrets",
        "calibrating the arm (it is fine)", "waiting on code review (from a pigeon)", "pushing to main, confidently",
    )

    private fun stamp(): String = "%02d:%02d:%02d".format(Random.nextInt(24), Random.nextInt(60), Random.nextInt(60))
    private fun line() = "${stamp()} ${Sw.pick(fake)}"

    private fun lines(ctx: Context): List<String> =
        (Sw.p(ctx).getString("busy_log", "") ?: "").split('\n').filter { it.isNotBlank() }.takeLast(6)

    private fun show(rv: RemoteViews, log: List<String>) {
        val shown = List(6 - log.size) { "" } + log
        ids.forEachIndexed { i, id -> rv.setTextViewText(id, shown[i]) }
    }

    override fun fill(ctx: Context, rv: RemoteViews) {
        val log = lines(ctx).ifEmpty { listOf("$ tap to look busy", "$ nobody will ask") }
        show(rv, log)
        rv.setTextViewText(R.id.lbl, "LOOK BUSY · TAP")
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "busy"))
    }

    override fun act(ctx: Context, what: String) {
        var log = lines(ctx)
        for (i in 0 until 16) {
            log = (log + line()).takeLast(6)
            frame(ctx) { rv -> show(rv, log); rv.setTextViewText(R.id.lbl, "● BUILDING " + "▮".repeat(1 + i / 2)) }
            pause(380)
        }
        log = (log + "${stamp()} BUILD SUCCESSFUL (nobody checked)").takeLast(6)
        Sw.p(ctx).edit().putString("busy_log", log.joinToString("\n")).apply()
        frame(ctx) { rv -> show(rv, log); rv.setTextViewText(R.id.lbl, "✔ DONE · TAP TO LOOK BUSY AGAIN") }
        pause(400)
    }
}

/** Coin and dice. He judges the result. */
class WidgetCoin : SillyWidget() {
    override val layout = R.layout.widget_coin
    private val coinLines = listOf("the coin has spoken. it lies sometimes.", "fate says so (fate is bored)", "best of three? no.", "i flipped it, you decide if you agree")
    private val dice = mapOf(
        1 to "critical fail. iconic.", 2 to "technically a number", 3 to "mid. respect.", 4 to "decent. not great, not terrible",
        5 to "oooh, so close", 6 to "NAT 6. unbearable.",
    )

    override fun fill(ctx: Context, rv: RemoteViews) {
        val p = Sw.p(ctx)
        rv.setTextViewText(R.id.lbl, "COIN & DICE")
        rv.setTextColor(R.id.lbl, 0xFF5DFF7A.toInt())
        rv.setTextViewText(R.id.res, p.getString("coin_res", "flip or roll") ?: "flip or roll")
        rv.setTextViewText(R.id.sub, p.getString("coin_sub", "he will judge you") ?: "he will judge you")
        rv.setOnClickPendingIntent(R.id.bcoin, Sw.tap(ctx, javaClass, "coin"))
        rv.setOnClickPendingIntent(R.id.bdice, Sw.tap(ctx, javaClass, "dice"))
    }

    override fun act(ctx: Context, what: String) {
        val coin = what == "coin"
        val faces = if (coin) listOf("HEADS", "TAILS") else listOf("1", "2", "3", "4", "5", "6")
        val finalRes = Sw.pick(faces)
        for (i in 0 until 10) {
            val shown = if (i == 9) finalRes else if (coin) faces[i % 2] else Sw.pick(faces)
            frame(ctx) { rv -> rv.setTextViewText(R.id.res, (if (coin) "🪙 " else "🎲 ") + shown); rv.setTextViewText(R.id.sub, "...") }
            pause(55L + i * i * 6L)
        }
        val say = if (coin) Sw.pick(coinLines) else dice.getValue(finalRes.toInt())
        val res = (if (coin) "🪙 " else "🎲 ") + finalRes
        Sw.p(ctx).edit().putString("coin_res", res).putString("coin_sub", say).apply()
        frame(ctx) { rv -> rv.setTextViewText(R.id.res, res); rv.setTextViewText(R.id.sub, say) }
    }
}

/** Your phone's battery as his feelings. Reads the battery itself, no link to home needed. */
class WidgetBattery : SillyWidget() {
    override val layout = R.layout.widget_battery

    private fun lines(level: Int, charging: Boolean): Pair<String, String> {
        if (charging) return "excited" to Sw.pick(listOf("nom nom electricity", "plugged in. i can see colours", "charging, do not disturb the snack"))
        return when {
            level >= 90 -> "excited" to Sw.pick(listOf("FULL POWER. i could fight a cloud", "hyper. send help"))
            level >= 60 -> "happy" to Sw.pick(listOf("vibing, battery-wise", "plenty left. be bold"))
            level >= 30 -> "rest" to Sw.pick(listOf("it is fine. it is fine.", "i can feel the number going down"))
            level >= 15 -> "nervous" to Sw.pick(listOf("please find a charger", "i am getting dramatic"))
            else -> "sad" to Sw.pick(listOf("tell my plants i loved them", "going dark. avenge me", "this is how it ends"))
        }
    }

    override fun fill(ctx: Context, rv: RemoteViews) {
        val b = ctx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = b?.let { it.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) * 100 / maxOf(1, it.getIntExtra(BatteryManager.EXTRA_SCALE, 100)) } ?: 50
        val charging = (b?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0) != 0
        val (mood, line) = lines(level, charging)
        Sw.loop(ctx, rv, mood)
        rv.setTextViewText(R.id.lbl, (if (charging) "⚡ " else "") + "ENERGY · $level%")
        rv.setTextColor(R.id.lbl, if (level < 15 && !charging) 0xFFF08A73.toInt() else 0xFF5DFF7A.toInt())
        rv.setTextViewText(R.id.title, line)
        rv.setOnClickPendingIntent(R.id.root, Sw.tap(ctx, javaClass, "poke"))
    }

    override fun act(ctx: Context, what: String) {
        for (m in listOf("startle", "confused")) {
            frame(ctx) { rv -> Sw.still(ctx, rv, m) }
            pause(350)
        }
    }
}

/** A nap timer he sleeps through with you. The countdown is a Chronometer, which ticks inside the launcher. */
class WidgetNap : SillyWidget() {
    override val layout = R.layout.widget_nap

    override fun fill(ctx: Context, rv: RemoteViews) {
        val end = Sw.p(ctx).getLong("nap_end", 0)
        val left = end - System.currentTimeMillis()
        val napping = left > 0
        Sw.loop(ctx, rv, if (napping) "sleepy" else "rest")
        rv.setTextViewText(R.id.lbl, if (napping) "SHHH · NAPPING" else "NAP TIMER · PICK MINUTES")
        rv.setTextColor(R.id.lbl, 0xFF5DFF7A.toInt())
        rv.setViewVisibility(R.id.chrono, if (napping) View.VISIBLE else View.GONE)
        rv.setViewVisibility(R.id.zzz, if (napping) View.VISIBLE else View.GONE)
        if (napping) {
            rv.setChronometerCountDown(R.id.chrono, true)
            rv.setChronometer(R.id.chrono, SystemClock.elapsedRealtime() + left, null, true)
        }
        for (id in intArrayOf(R.id.b10, R.id.b20, R.id.b45)) rv.setViewVisibility(id, if (napping) View.GONE else View.VISIBLE)
        rv.setViewVisibility(R.id.bwake, if (napping) View.VISIBLE else View.GONE)
        rv.setOnClickPendingIntent(R.id.b10, Sw.tap(ctx, javaClass, "n10"))
        rv.setOnClickPendingIntent(R.id.b20, Sw.tap(ctx, javaClass, "n20"))
        rv.setOnClickPendingIntent(R.id.b45, Sw.tap(ctx, javaClass, "n45"))
        rv.setOnClickPendingIntent(R.id.bwake, Sw.tap(ctx, javaClass, "wake"))
    }

    private fun alarm(ctx: Context) = PendingIntent.getBroadcast(
        ctx, 7101, Intent(ctx, NapDone::class.java), PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
    )

    override fun act(ctx: Context, what: String) {
        val am = ctx.getSystemService(Context.ALARM_SERVICE) as AlarmManager
        if (what == "wake") {
            am.cancel(alarm(ctx))
            Sw.p(ctx).edit().putLong("nap_end", 0).apply()
            return
        }
        val min = what.removePrefix("n").toIntOrNull() ?: return
        val end = System.currentTimeMillis() + min * 60_000L
        Sw.p(ctx).edit().putLong("nap_end", end).apply()
        am.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, end, alarm(ctx))
        for (m in listOf("yawn", "sleepy")) {
            frame(ctx) { rv -> Sw.still(ctx, rv, m) }
            pause(450)
        }
    }
}

/** The nap ended: stretch, and say so (a notification, in case the phone is face down). */
class NapDone : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        Sw.p(ctx).edit().putLong("nap_end", 0).apply()
        Notifier.show(ctx, "reminder", "Nap over", "wake up, nerd. he is already stretching.", "happy", "nap:done")
        val pr = goAsync()
        Thread {
            try {
                val nap = WidgetNap()
                val mgr = AppWidgetManager.getInstance(ctx)
                val ids = mgr.getAppWidgetIds(ComponentName(ctx, WidgetNap::class.java))
                for (m in listOf("yawn", "stretch", "happy")) {
                    if (ids.isNotEmpty()) mgr.partiallyUpdateAppWidget(ids, RemoteViews(ctx.packageName, R.layout.widget_nap).also { Sw.still(ctx, it, m) })
                    Thread.sleep(500)
                }
                nap.redraw(ctx)
            } catch (_: Exception) {
            } finally { pr.finish() }
        }.start()
    }
}
