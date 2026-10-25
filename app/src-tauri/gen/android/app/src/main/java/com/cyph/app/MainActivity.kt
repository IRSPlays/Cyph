package com.cyph.app

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.webkit.JavascriptInterface
import android.webkit.WebView
import androidx.activity.enableEdgeToEdge
import androidx.core.app.NotificationManagerCompat
import org.json.JSONArray
import org.json.JSONObject

class MainActivity : TauriActivity() {
  override fun onCreate(savedInstanceState: Bundle?) {
    enableEdgeToEdge()
    super.onCreate(savedInstanceState)
    Notifier.ensureChannels(this)
    ListenService.start(this)
  }

  override fun onStart() { super.onStart(); AppState.visible = true }
  override fun onStop() { super.onStop(); AppState.visible = false }

  override fun onWebViewCreate(webView: WebView) {
    webView.addJavascriptInterface(Bridge(this), "CyphNative")
  }

  /** What the page can ask of the phone: widgets, reminders and notifications. Strings in, strings out. */
  class Bridge(private val a: MainActivity) {
    @JavascriptInterface fun snapshot(json: String) {
      a.getSharedPreferences("cyph_native", Context.MODE_PRIVATE).edit().putString("snap", json).apply()
      Widgets.updateAll(a)
    }

    @JavascriptInterface fun schedule(json: String) {
      try { Scheduler.set(a, JSONArray(json)) } catch (_: Exception) { }
    }

    @JavascriptInterface fun notify(json: String) {
      try {
        val o = JSONObject(json)
        Notifier.show(a, o.optString("kind", "quiet"), o.optString("title", "Cyph"), o.optString("text"), o.optString("mood", "rest"), o.optString("key", "n" + System.nanoTime()))
      } catch (_: Exception) { }
    }

    /** "granted" or "denied" for showing notifications */
    @JavascriptInterface fun notifyState(): String = if (NotificationManagerCompat.from(a).areNotificationsEnabled()) "granted" else "denied"

    @JavascriptInterface fun askNotify() {
      if (Build.VERSION.SDK_INT >= 33 && a.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
        a.runOnUiThread { a.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), 41) }
      }
    }

    @JavascriptInterface fun listenState(): Boolean = AppState.enabled(a)
    @JavascriptInterface fun setListen(on: Boolean) { AppState.setEnabled(a, on); if (on) ListenService.start(a) else ListenService.stop(a) }

    /** The system page for the quiet "listening" line: Android only lets the user hide it, so we take them there */
    @JavascriptInterface fun openServiceChannel() {
      try {
        a.startActivity(Intent(Settings.ACTION_CHANNEL_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, a.packageName).putExtra(Settings.EXTRA_CHANNEL_ID, "cyph_service").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
      } catch (_: Exception) { }
    }

    /** Xiaomi's own "autostart" screen, where it decides whether an app may wake up for an alarm; else the app's settings page */
    @JavascriptInterface fun openAutostart() {
      val tries = listOf(
        Intent().setComponent(ComponentName("com.miui.securitycenter", "com.miui.permcenter.autostart.AutoStartManagementActivity")),
        Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).setData(Uri.parse("package:" + a.packageName)),
      )
      for (i in tries) { try { a.startActivity(i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)); return } catch (_: Exception) { } }
    }
  }
}
