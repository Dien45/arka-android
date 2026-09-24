package id.arka.app

import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.Gravity
import android.view.View
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var web: WebView
    private lateinit var boot: LinearLayout
    private lateinit var status: TextView

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val root = LinearLayout(this)
        root.orientation = LinearLayout.VERTICAL
        root.setBackgroundColor(Color.parseColor("#101114"))

        boot = LinearLayout(this)
        boot.orientation = LinearLayout.VERTICAL
        boot.gravity = Gravity.CENTER
        boot.setPadding(48, 48, 48, 48)
        boot.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.MATCH_PARENT
        )
        val brand = TextView(this)
        brand.text = "Arka"
        brand.setTextColor(Color.parseColor("#ff9a62"))
        brand.textSize = 28f
        brand.gravity = Gravity.CENTER
        boot.addView(brand)
        val spin = ProgressBar(this)
        val sp = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT,
            LinearLayout.LayoutParams.WRAP_CONTENT
        )
        sp.gravity = Gravity.CENTER_HORIZONTAL
        sp.topMargin = 28
        spin.layoutParams = sp
        boot.addView(spin)
        status = TextView(this)
        status.text = "Menyalakan agen…"
        status.setTextColor(Color.parseColor("#c5c9d3"))
        status.textSize = 15f
        status.gravity = Gravity.CENTER
        status.setPadding(0, 20, 0, 0)
        boot.addView(status)

        web = WebView(this)
        web.visibility = View.GONE
        web.layoutParams = LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.MATCH_PARENT
        )
        val ws: WebSettings = web.settings
        ws.javaScriptEnabled = true
        ws.domStorageEnabled = true
        ws.allowFileAccess = true
        web.webViewClient = WebViewClient()
        web.webChromeClient = WebChromeClient()

        root.addView(boot)
        root.addView(web)
        setContentView(root)

        val svc = Intent(this, ArkaService::class.java)
        if (Build.VERSION.SDK_INT >= 26) startForegroundService(svc) else startService(svc)

        Thread { waitForServer() }.start()
    }

    private fun waitForServer() {
        for (i in 0 until 90) {
            if (up()) {
                ui.post {
                    boot.visibility = View.GONE
                    web.visibility = View.VISIBLE
                    web.loadUrl("http://127.0.0.1:8765")
                }
                return
            }
            if (i == 12) ui.post { status.text = "Masih menyiapkan Python…" }
            Thread.sleep(1000)
        }
        ui.post { status.text = "Gagal menyala. Tutup app, buka lagi. Cek notifikasi Arka." }
    }

    private fun up(): Boolean {
        var c: HttpURLConnection? = null
        return try {
            c = URL("http://127.0.0.1:8765/api/health").openConnection() as HttpURLConnection
            c.connectTimeout = 800
            c.readTimeout = 800
            c.connect()
            c.responseCode < 500
        } catch (_: Exception) {
            false
        } finally {
            c?.disconnect()
        }
    }
}
