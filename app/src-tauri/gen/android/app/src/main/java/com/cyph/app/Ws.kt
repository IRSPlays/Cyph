package com.cyph.app

import android.content.Context
import org.json.JSONObject
import java.io.DataInputStream
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.net.SocketTimeoutException
import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.X509TrustManager

/** Where the server is and how to talk to it: the file the app's Rust side saves when the phone is paired. The token is
 *  read only to authenticate and is never logged. */
class Server(val host: String, val fp: String, val token: String, val deviceId: String) {
    companion object {
        fun load(ctx: Context): Server? = try {
            val f = listOf(File(ctx.dataDir, "settings.json"), File(ctx.filesDir, "settings.json")).first { it.exists() }
            val o = JSONObject(f.readText())
            val t = o.optString("token")
            if (t.isBlank() || o.optString("host").isBlank() || o.optString("fp").isBlank()) null
            else Server(o.getString("host"), o.getString("fp"), t, o.optString("device_id"))
        } catch (_: Exception) { null }
    }
}

/** A small WebSocket client over TLS that trusts exactly one certificate (the server's, by its SHA-256, as in the pairing
 *  link). No library: the protocol needed here is a handshake, text frames, ping and close. */
class Ws(private val server: Server) : AutoCloseable {
    private lateinit var sock: SSLSocket
    private lateinit var inp: DataInputStream
    private lateinit var out: OutputStream
    private val rnd = SecureRandom()

    private fun pinned(): X509TrustManager {
        val want = server.fp.lowercase().filter { it in '0'..'9' || it in 'a'..'f' }
        return object : X509TrustManager {
            override fun checkClientTrusted(c: Array<X509Certificate>, a: String) = throw CertificateException("no client auth")
            override fun checkServerTrusted(c: Array<X509Certificate>, a: String) {
                val got = MessageDigest.getInstance("SHA-256").digest(c[0].encoded).joinToString("") { "%02x".format(it) }
                if (got != want) throw CertificateException("server certificate is not the paired one")
            }
            override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
        }
    }

    fun connect(path: String) {
        val (h, p) = server.host.substringBeforeLast(":") to server.host.substringAfterLast(":").toInt()
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf(pinned()), rnd)
        val raw = Socket()
        raw.connect(InetSocketAddress(h, p), 10_000)
        sock = ctx.socketFactory.createSocket(raw, h, p, true) as SSLSocket
        sock.soTimeout = 10_000
        sock.startHandshake()
        inp = DataInputStream(sock.inputStream.buffered())
        out = sock.outputStream
        val key = ByteArray(16).also { rnd.nextBytes(it) }.let { android.util.Base64.encodeToString(it, android.util.Base64.NO_WRAP) }
        out.write("GET $path HTTP/1.1\r\nHost: ${server.host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: $key\r\nSec-WebSocket-Version: 13\r\n\r\n".toByteArray())
        out.flush()
        val head = StringBuilder()
        while (!head.endsWith("\r\n\r\n")) { head.append(inp.read().also { if (it < 0) throw java.io.IOException("closed during handshake") }.toChar()); if (head.length > 8192) throw java.io.IOException("bad handshake") }
        if (!head.startsWith("HTTP/1.1 101")) throw java.io.IOException("server refused the websocket")
    }

    @Synchronized fun send(text: String) {
        val data = text.toByteArray()
        val mask = ByteArray(4).also { rnd.nextBytes(it) }
        val hdr = java.io.ByteArrayOutputStream()
        hdr.write(0x81)
        when {
            data.size < 126 -> hdr.write(0x80 or data.size)
            data.size < 65536 -> { hdr.write(0x80 or 126); hdr.write(data.size shr 8); hdr.write(data.size and 0xff) }
            else -> { hdr.write(0x80 or 127); for (i in 7 downTo 0) hdr.write(((data.size.toLong() shr (8 * i)) and 0xff).toInt()) }
        }
        hdr.write(mask)
        for (i in data.indices) hdr.write(data[i].toInt() xor mask[i % 4].toInt())
        out.write(hdr.toByteArray())
        out.flush()
    }

    /** The next text message, or null when the server closed. A quiet line for `idleMs` throws SocketTimeoutException
     *  (the caller pings and tries again). */
    fun recv(idleMs: Int): String? {
        val buf = java.io.ByteArrayOutputStream()
        while (true) {
            sock.soTimeout = idleMs
            val b0 = try { inp.read() } catch (e: SocketTimeoutException) { if (buf.size() == 0) throw e else continue }
            if (b0 < 0) return null
            sock.soTimeout = 20_000                     // a frame that has begun must finish soon
            val b1 = inp.read()
            var len = (b1 and 0x7f).toLong()
            if (len == 126L) len = ((inp.read() shl 8) or inp.read()).toLong()
            else if (len == 127L) { len = 0; for (i in 0 until 8) len = (len shl 8) or inp.read().toLong() }
            if (len > 8_000_000) throw java.io.IOException("frame too large")
            val payload = ByteArray(len.toInt()); inp.readFully(payload)
            when (b0 and 0x0f) {
                0x8 -> return null
                0x9 -> { synchronized(this) { out.write(byteArrayOf(0x8A.toByte(), 0x80.toByte(), 0, 0, 0, 0)); out.flush() } }
                0x1, 0x0 -> { buf.write(payload); if (b0 and 0x80 != 0) return buf.toString("UTF-8") }
                else -> {}
            }
        }
    }

    override fun close() { try { sock.close() } catch (_: Exception) { } }
}
