package id.arka.app

import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var web: WebView
    private lateinit var boot: LinearLayout
    private lateinit var status: TextView
    private lateinit var gear: Button

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = Color.parseColor("#101114")
        window.navigationBarColor = Color.parseColor("#101114")

        val root = FrameLayout(this)
        root.setBackgroundColor(Color.parseColor("#101114"))
        root.layoutParams = ViewGroup.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.MATCH_PARENT
        )

        boot = LinearLayout(this)
        boot.orientation = LinearLayout.VERTICAL
        boot.gravity = Gravity.CENTER
        boot.setBackgroundColor(Color.parseColor("#101114"))
        boot.setPadding(48, 48, 48, 48)
        val brand = TextView(this)
        brand.text = "Arka"
        brand.setTextColor(Color.parseColor("#ff9a62"))
        brand.textSize = 28f
        brand.gravity = Gravity.CENTER
        boot.addView(brand)
        val spin = ProgressBar(this)
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
        web.setBackgroundColor(Color.parseColor("#101114"))
        val ws: WebSettings = web.settings
        ws.javaScriptEnabled = true
        ws.domStorageEnabled = true
        ws.allowFileAccess = true
        ws.useWideViewPort = false
        ws.loadWithOverviewMode = false
        web.webChromeClient = WebChromeClient()
        web.webViewClient = object : WebViewClient() {
            override fun onPageFinished(view: WebView?, url: String?) {
                view?.evaluateJavascript(FIT_JS, null)
            }
        }

        gear = Button(this)
        gear.text = "Pengaturan"
        gear.setTextColor(Color.WHITE)
        gear.setBackgroundColor(Color.parseColor("#ff6b35"))
        gear.visibility = View.GONE
        gear.setOnClickListener { showSettings() }

        val webLp = FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.MATCH_PARENT
        )
        val bootLp = FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.MATCH_PARENT,
            FrameLayout.LayoutParams.MATCH_PARENT
        )
        val gearLp = FrameLayout.LayoutParams(
            FrameLayout.LayoutParams.WRAP_CONTENT,
            FrameLayout.LayoutParams.WRAP_CONTENT,
            Gravity.TOP or Gravity.END
        )
        gearLp.topMargin = 12
        gearLp.rightMargin = 12

        root.addView(web, webLp)
        root.addView(boot, bootLp)
        root.addView(gear, gearLp)
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
                    gear.visibility = View.VISIBLE
                    web.loadUrl("http://127.0.0.1:8765")
                }
                return
            }
            if (i == 12) ui.post { status.text = "Masih menyiapkan Python…" }
            Thread.sleep(1000)
        }
        ui.post { status.text = "Gagal menyala. Tutup app, buka lagi." }
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

    private fun showSettings() {
        val box = LinearLayout(this)
        box.orientation = LinearLayout.VERTICAL
        box.setPadding(40, 24, 40, 8)
        fun field(hint: String, password: Boolean = false): EditText {
            val e = EditText(this)
            e.hint = hint
            e.setTextColor(Color.parseColor("#111111"))
            e.setHintTextColor(Color.parseColor("#666666"))
            if (password) e.inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD
            box.addView(e)
            return e
        }
        val base = field("API base URL")
        val model = field("Model")
        val key = field("API key", true)
        val ws = field("Folder proyek")

        Thread {
            try {
                val raw = URL("http://127.0.0.1:8765/api/settings").readText()
                val j = JSONObject(raw)
                ui.post {
                    base.setText(j.optString("api_base"))
                    model.setText(j.optString("model"))
                    key.setText(j.optString("api_key"))
                    ws.setText(j.optString("workspace"))
                }
            } catch (_: Exception) {}
        }.start()

        AlertDialog.Builder(this)
            .setTitle("Pengaturan Arka")
            .setView(box)
            .setNegativeButton("Batal", null)
            .setPositiveButton("Simpan") { _, _ ->
                Thread {
                    try {
                        val body = JSONObject()
                            .put("api_base", base.text.toString().trim())
                            .put("model", model.text.toString().trim())
                            .put("api_key", key.text.toString().trim())
                            .put("workspace", ws.text.toString().trim())
                            .toString()
                        val c = URL("http://127.0.0.1:8765/api/settings").openConnection() as HttpURLConnection
                        c.requestMethod = "PUT"
                        c.setRequestProperty("Content-Type", "application/json")
                        c.doOutput = true
                        c.outputStream.use { it.write(body.toByteArray()) }
                        val ok = c.responseCode in 200..299
                        ui.post {
                            Toast.makeText(
                                this,
                                if (ok) "Tersimpan" else "Gagal simpan ${c.responseCode}",
                                Toast.LENGTH_SHORT
                            ).show()
                            if (ok) web.reload()
                        }
                        c.disconnect()
                    } catch (e: Exception) {
                        ui.post { Toast.makeText(this, e.message, Toast.LENGTH_LONG).show() }
                    }
                }.start()
            }
            .show()
    }

    companion object {
        private const val FIT_JS = """
            (function(){
              var s=document.createElement('style');
              s.textContent='#app{grid-template-columns:1fr!important;height:100%!important;} html,body{height:100%;margin:0;overflow:hidden;} .gutter,.files{display:none!important;} .sidebar{display:none!important;} .main{min-height:100%;} .topbar{padding-right:118px;}';
              document.documentElement.appendChild(s);
            })();
        """
    }
}
