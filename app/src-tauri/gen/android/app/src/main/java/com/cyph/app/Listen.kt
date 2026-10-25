package com.cyph.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import org.json.JSONObject

object AppState {
    /** true while the app's window is on screen: then he answers there, and there is nothing to notify */
    @Volatile var visible = false
    @Volatile var lastReplyHash = 0
    @Volatile var lastReplyAt = 0L
    fun enabled(ctx: Context) = ctx.getSharedPreferences("cyph_native", Context.MODE_PRIVATE).getBoolean("listen", true)
    fun setEnabled(ctx: Context, on: Boolean) = ctx.getSharedPreferences("cyph_native", Context.MODE_PRIVATE).edit().putBoolean("listen", on).apply()
}

private val TAG = Regex("^\\s*\\[([a-z]+)\\]\\s*")

/** "[smug] text" -> mood and text */
fun splitMood(raw: String): Pair<String, String> {
    val m = TAG.find(raw) ?: return "rest" to raw.trim()
    return m.groupValues[1] to raw.substring(m.range.last + 1).trim()
}

object Replies {
    const val KEY = "reply"

    fun show(ctx: Context, raw: String) {
        val (mood, body) = splitMood(raw)
        if (body.isBlank()) return
        val ri = androidx.core.app.RemoteInput.Builder("reply_text").setLabel("Say something back").build()
        val pi = PendingIntent.getBroadcast(
            ctx, 31, Intent(ctx, ReplyReceiver::class.java).setAction(ReplyReceiver.REPLY),
            PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 31) PendingIntent.FLAG_MUTABLE else 0),
        )
        val reply = NotificationCompat.Action.Builder(0, "REPLY", pi).addRemoteInput(ri).setAllowGeneratedReplies(true).build()
        Notifier.show(ctx, "reply", "", body.take(600), mood, KEY, nativeAction = reply)
    }
}

/** Answers typed straight into the notification: say it over a short chat connection, then show what he said. */
class ReplyReceiver : BroadcastReceiver() {
    override fun onReceive(ctx: Context, i: Intent) {
        if (i.action != REPLY) return
        val text = androidx.core.app.RemoteInput.getResultsFromIntent(i)?.getCharSequence("reply_text")?.toString()?.trim().orEmpty()
        if (text.isEmpty()) return
        val pending = goAsync()
        val app = ctx.applicationContext
        Notifier.show(app, "reply", "", "…", "think", Replies.KEY)
        Thread {
            try {
                val server = Server.load(app) ?: return@Thread
                Ws(server).use { ws ->
                    ws.connect("/chat")
                    ws.send(JSONObject().put("type", "auth").put("token", server.token).toString())
                    ws.send(JSONObject().put("type", "say").put("text", text).toString())
                    val until = System.currentTimeMillis() + 120_000
                    while (System.currentTimeMillis() < until) {
                        val msg = try { ws.recv(15_000) } catch (_: java.net.SocketTimeoutException) { continue } ?: break
                        val o = JSONObject(msg)
                        when (o.optString("type")) {
                            "final" -> {
                                AppState.lastReplyHash = o.optString("text").hashCode(); AppState.lastReplyAt = System.currentTimeMillis()
                                Replies.show(app, o.optString("text")); return@Thread
                            }
                            "busy" -> { Notifier.show(app, "reply", "", "I'm still answering the last thing. Try again in a moment.", "nervous", Replies.KEY); return@Thread }
                            "error" -> { Notifier.show(app, "alert", "Could not answer", o.optString("message", "Something went wrong."), "nervous", Replies.KEY); return@Thread }
                        }
                    }
                }
                Notifier.show(app, "alert", "No answer yet", "Cyph did not answer in time. Open the app to see.", "nervous", Replies.KEY)
            } catch (_: Exception) {
                Notifier.show(app, "alert", "Could not reach home", "The reply was not sent. Open the app to try again.", "nervous", Replies.KEY)
            } finally { pending.finish() }
        }.start()
    }

    companion object { const val REPLY = "com.cyph.app.REPLY" }
}

/** Keeps one connection to the server's change feed while the app is closed, so an answer to something you said (and then
 *  left) turns into a notification. A quiet, low-priority "listening" line is Android's price for staying alive. */
class ListenService : Service() {
    @Volatile private var running = false
    private var thread: Thread? = null

    override fun onBind(i: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        val nm = getSystemService(NotificationManager::class.java)
        if (Build.VERSION.SDK_INT >= 26 && nm.getNotificationChannel("cyph_service") == null) {
            val ch = NotificationChannel("cyph_service", "Listening for replies", NotificationManager.IMPORTANCE_MIN)
            ch.description = "The small line that lets Cyph tell you when he answers while the app is closed"
            nm.createNotificationChannel(ch)
        }
        val n = status("Listening for replies")
        if (Build.VERSION.SDK_INT >= 34) startForeground(11, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE) else startForeground(11, n)
        if (!running) {
            running = true
            thread = Thread { loop() }.also { it.isDaemon = true; it.start() }
        }
        return START_STICKY
    }

    private fun status(text: String): Notification =
        NotificationCompat.Builder(this, "cyph_service").setSmallIcon(R.drawable.ic_stat_cyph).setContentTitle("Cyph").setContentText(text)
            .setOngoing(true).setPriority(NotificationCompat.PRIORITY_MIN).setContentIntent(Notifier.open(this)).build()

    private fun say(text: String) = getSystemService(NotificationManager::class.java).notify(11, status(text))

    private fun loop() {
        var wait = 3_000L
        while (running) {
            val server = Server.load(this)
            if (server == null) { Thread.sleep(30_000); continue }
            try {
                Ws(server).use { ws ->
                    ws.connect("/events")
                    ws.send(JSONObject().put("type", "auth").put("token", server.token).toString())
                    var me = server.deviceId
                    say("Listening for replies")
                    wait = 3_000L
                    var idle = 0
                    while (running) {
                        val msg: String? = try { ws.recv(25_000) } catch (_: java.net.SocketTimeoutException) {
                            idle++
                            if (idle > 4) throw java.io.IOException("no answer to ping")
                            ws.send("{\"type\":\"ping\"}")
                            continue
                        }
                        if (msg == null) break
                        idle = 0
                        val o = JSONObject(msg)
                        when (o.optString("type")) {
                            "ready" -> if (o.optString("id").isNotBlank()) me = o.getString("id")
                            "chat" -> {
                                val ev = o.optJSONObject("ev")
                                if (ev != null && ev.optString("type") == "final" && o.optString("from") == me && !AppState.visible) {
                                    val text = ev.optString("text")
                                    // the inline REPLY already showed this one
                                    if (!(text.hashCode() == AppState.lastReplyHash && System.currentTimeMillis() - AppState.lastReplyAt < 30_000)) Replies.show(this, text)
                                }
                            }
                        }
                    }
                }
            } catch (_: Exception) { }
            if (!running) break
            say("Reconnecting…")
            try { Thread.sleep(wait) } catch (_: InterruptedException) { }
            wait = minOf(wait * 2, 60_000L)
        }
    }

    override fun onDestroy() { running = false; super.onDestroy() }

    companion object {
        fun start(ctx: Context) {
            if (!AppState.enabled(ctx) || Server.load(ctx) == null) return
            try {
                val i = Intent(ctx, ListenService::class.java)
                if (Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(i) else ctx.startService(i)
            } catch (_: Exception) { /* Android refused a background start: it starts again next time the app opens */ }
        }
        fun stop(ctx: Context) { ctx.stopService(Intent(ctx, ListenService::class.java)) }
    }
}
