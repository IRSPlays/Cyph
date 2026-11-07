package com.cyph.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.PathMeasure
import android.graphics.RectF

/** The glowing edge of a widget, used as its progress readout: a dim track all the way round and a bright arc from the
 *  top centre, clockwise, for the fraction `p`. A launcher cannot draw a custom stroke, so it is painted into a bitmap
 *  the size of the widget and laid under the content. */
object Edge {
    fun draw(ctx: Context, wDp: Int, hDp: Int, p: Float, color: Int): Bitmap {
        val d = ctx.resources.displayMetrics.density
        val s = minOf(d, 560f / maxOf(wDp, hDp, 1)) // keep the bitmap small: it travels inside the widget update
        val w = (wDp * s).toInt().coerceAtLeast(48)
        val h = (hDp * s).toInt().coerceAtLeast(48)
        val bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        val sw = 3.5f * s
        val inset = sw * 1.9f
        val path = perimeter(inset, inset, w - inset, h - inset, 28f * s - inset)
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = sw; strokeCap = Paint.Cap.ROUND; strokeJoin = Paint.Join.ROUND }
        paint.color = color
        paint.alpha = 48
        c.drawPath(path, paint)
        val frac = p.coerceIn(0f, 1f)
        if (frac > 0.003f) {
            val pm = PathMeasure(path, false)
            val len = pm.length
            val seg = Path()
            pm.getSegment(0f, len * frac, seg, true)
            paint.color = color
            paint.alpha = 64
            paint.strokeWidth = sw * 2.8f
            c.drawPath(seg, paint)
            paint.alpha = 255
            paint.strokeWidth = sw
            c.drawPath(seg, paint)
            if (frac < 0.99f) { // a bright head on the arc, so the progress reads as a place and not just a colour
                val pos = FloatArray(2)
                pm.getPosTan(len * frac, pos, null)
                paint.style = Paint.Style.FILL
                paint.color = 0xFFFFFFFF.toInt()
                paint.alpha = 235
                c.drawCircle(pos[0], pos[1], sw * 0.95f, paint)
            }
        }
        return bmp
    }

    private fun perimeter(l: Float, t: Float, r: Float, b: Float, rad: Float): Path {
        val cx = (l + r) / 2
        return Path().apply {
            moveTo(cx, t)
            lineTo(r - rad, t)
            arcTo(RectF(r - 2 * rad, t, r, t + 2 * rad), -90f, 90f)
            lineTo(r, b - rad)
            arcTo(RectF(r - 2 * rad, b - 2 * rad, r, b), 0f, 90f)
            lineTo(l + rad, b)
            arcTo(RectF(l, b - 2 * rad, l + 2 * rad, b), 90f, 90f)
            lineTo(l, t + rad)
            arcTo(RectF(l, t, l + 2 * rad, t + 2 * rad), 180f, 90f)
            lineTo(cx, t)
        }
    }
}
