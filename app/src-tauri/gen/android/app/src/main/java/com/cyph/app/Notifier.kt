package com.cyph.app

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import android.widget.RemoteViews
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** Cyph's notifications: six kinds, each its own channel (so one can be silenced alone), one dark card look.
 *  The edge colour of the avatar ring says what kind it is, the same colours as the widgets. */
object Notifier {
    class Kind(val id: String, val code: String, val label: String, val importance: Int, val colour: Int, val nameRes: Int)

    val KINDS = listOf(
        Kind("reply", "p", "CYPH", NotificationManager.IMPORTANCE_HIGH, 0xFF5DFF7A.toInt(), R.string.ch_reply),
        Kind("nudge", "n", "NUDGE", NotificationManager.IMPORTANCE_HIGH, 0xFF5DFF7A.toInt(), R.string.ch_nudge),
        Kind("reminder", "r", "REMINDER", NotificationManager.IMPORTANCE_HIGH, 0xFFD9984A.toInt(), R.string.ch_reminder),
        Kind("arm", "a", "ARM", NotificationManager.IMPORTANCE_DEFAULT, 0xFF2F9D8F.toInt(), R.string.ch_arm),
        Kind("study", "s", "STUDY", NotificationManager.IMPORTANCE_DEFAULT, 0xFF5B9FB8.toInt(), R.string.ch_study),
        Kind("alert", "e", "ALERT", NotificationManager.IMPORTANCE_HIGH, 0xFFF08A73.toInt(), R.string.ch_alert),
        Kind("quiet", "q", "QUIET", NotificationManager.IMPORTANCE_LOW, 0xFF8A9A78.toInt(), R.string.ch_quiet),
    )

    fun kind(id: String) = KINDS.firstOrNull { it.id == id } ?: KINDS[0]

    fun ensureChannels(ctx: Context) {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.O) return
        val nm = ctx.getSystemService(NotificationManager::class.java)
        for (k in KINDS) {
            if (nm.getNotificationChannel("cyph_" + k.id) == null) {
                val ch = NotificationChannel("cyph_" + k.id, ctx.getString(k.nameRes), k.importance)
                ch.description = "Cyph: " + ctx.getString(k.nameRes).lowercase()
                ch.enableLights(true)
                ch.lightColor = k.colour
                nm.createNotificationChannel(ch)
            }
        }
    }

    fun open(ctx: Context): PendingIntent =
        PendingIntent.getActivity(
            ctx, 0, Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )

    fun face(ctx: Context, mood: String): Int {
        val id = ctx.resources.getIdentifier("face_$mood", "drawable", ctx.packageName)
        return if (id != 0) id else R.drawable.face_rest
    }

    /** Shows one notification now. `idKey` makes a later one with the same key replace the earlier one. */
    fun show(ctx: Context, kindId: String, title: String, text: String, mood: String, idKey: String, atMs: Long = System.currentTimeMillis(), nativeAction: NotificationCompat.Action? = null) {
        if (!NotificationManagerCompat.from(ctx).areNotificationsEnabled()) return
        ensureChannels(ctx)
        val k = kind(kindId)
        val card = RemoteViews(ctx.packageName, R.layout.notif_card)
        card.setImageViewResource(R.id.nring, ctx.resources.getIdentifier("nt_ring_" + k.code, "drawable", ctx.packageName))
        card.setImageViewResource(R.id.nface, face(ctx, mood))
        card.setTextViewText(R.id.nkind, k.label)
        card.setTextColor(R.id.nkind, k.colour)
        card.setTextViewText(R.id.nwhen, SimpleDateFormat("HH:mm", Locale.US).format(Date(atMs)))
        val reply = kindId == "reply"
        // an answer from him is the whole point: show it big and long. Everything else keeps the short two-line look.
        card.setTextViewText(R.id.ntitle, if (reply) text else title)
        card.setInt(R.id.ntitle, "setMaxLines", if (reply) 8 else 2)
        card.setTextViewText(R.id.ntext, if (reply) "" else text)
        card.setViewVisibility(R.id.ntext, if (reply || text.isBlank()) android.view.View.GONE else android.view.View.VISIBLE)
        card.setViewVisibility(R.id.nwhen, android.view.View.VISIBLE)
        card.setViewVisibility(R.id.chips, android.view.View.GONE)

        val b = NotificationCompat.Builder(ctx, "cyph_" + k.id)
            .setSmallIcon(R.drawable.ic_stat_cyph)
            .setColor(k.colour)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(NotificationCompat.DecoratedCustomViewStyle())
            .setCustomContentView(card)
            .setCustomBigContentView(card)
            .setContentIntent(open(ctx))
            .setAutoCancel(true)
            .setWhen(atMs)
            .setPriority(if (k.importance == NotificationManager.IMPORTANCE_LOW) NotificationCompat.PRIORITY_LOW else NotificationCompat.PRIORITY_HIGH)
            .setCategory(if (kindId == "reminder") NotificationCompat.CATEGORY_REMINDER else NotificationCompat.CATEGORY_MESSAGE)
        if (kindId == "reminder" || kindId == "nudge") {
            val snooze = Intent(ctx, ActionReceiver::class.java).setAction(ActionReceiver.SNOOZE)
                .putExtra("kind", kindId).putExtra("title", title).putExtra("text", text).putExtra("mood", mood).putExtra("key", idKey)
            card.setViewVisibility(R.id.chips, android.view.View.VISIBLE)
            card.setTextViewText(R.id.act1, "SNOOZE 10")
            card.setTextViewText(R.id.act2, "OPEN")
            card.setOnClickPendingIntent(R.id.act1, PendingIntent.getBroadcast(ctx, idKey.hashCode(), snooze, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            card.setOnClickPendingIntent(R.id.act2, open(ctx))
        }
        if (nativeAction != null) b.addAction(nativeAction)
        try {
            NotificationManagerCompat.from(ctx).notify(idKey.hashCode(), b.build())
        } catch (_: SecurityException) {
            // the notification permission was taken away between the check and now
        }
    }
}
