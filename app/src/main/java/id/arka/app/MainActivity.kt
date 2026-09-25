package id.arka.app

import android.app.AlertDialog
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.provider.DocumentsContract
import android.text.InputType
import android.view.Gravity
import android.view.View
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.ProgressBar
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {
    private val ui = Handler(Looper.getMainLooper())
    private lateinit var web: WebView
    private lateinit var boot: LinearLayout
    private lateinit var status: TextView
    private lateinit var gear: Button
    private var folderField: EditText? = null
    private val defaultWs: String
        get() = File(filesDir, "proyek").absolutePath

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
        web.addJavascriptInterface(ArkaNative(), "ArkaNative")
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
        gear.setOnClickListener {
            web.evaluateJavascript(
                "(function(){var m=document.getElementById('settingsModal'); if(m) m.hidden=false;})();",
                null
            )
        }

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
                    gear.visibility = View.GONE
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
        val scroll = ScrollView(this)
        val box = LinearLayout(this)
        box.orientation = LinearLayout.VERTICAL
        box.setPadding(40, 24, 40, 8)
        scroll.addView(box)

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
        val model = field("Model (auto, gpt-4o-mini, sdxl, flux)")
        val key = field("API key", true)
        val ws = field("Folder proyek")
        folderField = ws
        val pick = Button(this)
        pick.text = "Pilih folder…"
        pick.setOnClickListener { launchTreePicker() }
        box.addView(pick)

        val ghUser = field("GitHub Username")
        val ghToken = field("GitHub Token (PAT)", true)
        val ghRepo = field("GitHub Repo (Dien45/arka-android)")

        Thread {
            try {
                val raw = URL("http://127.0.0.1:8765/api/settings").readText()
                val j = JSONObject(raw)
                ui.post {
                    base.setText(j.optString("api_base"))
                    model.setText(j.optString("model"))
                    val k = j.optString("api_key")
                    key.setText(if (k.equals("Tutup", ignoreCase = true)) "" else k)
                    ws.setText(j.optString("workspace"))
                    ghUser.setText(j.optString("github_username"))
                    ghToken.setText(j.optString("github_token"))
                    ghRepo.setText(j.optString("github_repo", "Dien45/arka-android"))
                }
            } catch (_: Exception) {}
        }.start()

        AlertDialog.Builder(this)
            .setTitle("Pengaturan Arka")
            .setView(scroll)
            .setNegativeButton("Batal", null)
            .setPositiveButton("Simpan") { _, _ ->
                Thread {
                    try {
                        var folder = ws.text.toString().trim()
                        if (folder.isEmpty()) folder = defaultWs
                        File(folder).mkdirs()
                        val body = JSONObject()
                            .put("api_base", base.text.toString().trim())
                            .put("model", model.text.toString().trim())
                            .put("api_key", key.text.toString().trim())
                            .put("workspace", folder)
                            .put("github_username", ghUser.text.toString().trim())
                            .put("github_token", ghToken.text.toString().trim())
                            .put("github_repo", ghRepo.text.toString().trim().ifEmpty { "Dien45/arka-android" })
                            .toString()
                        val c = URL("http://127.0.0.1:8765/api/settings").openConnection() as HttpURLConnection
                        c.requestMethod = "POST"
                        c.setRequestProperty("Content-Type", "application/json")
                        c.doOutput = true
                        c.outputStream.use { it.write(body.toByteArray()) }
                        val code = c.responseCode
                        val err = try {
                            (if (code >= 400) c.errorStream else c.inputStream)?.bufferedReader()?.readText() ?: ""
                        } catch (_: Exception) { "" }
                        ui.post {
                            Toast.makeText(
                                this,
                                if (code in 200..299) "Tersimpan" else "Gagal simpan: ${err.ifBlank { code.toString() }}",
                                Toast.LENGTH_LONG
                            ).show()
                            if (code in 200..299) web.reload()
                        }
                        c.disconnect()
                    } catch (e: Exception) {
                        ui.post { Toast.makeText(this, e.message, Toast.LENGTH_LONG).show() }
                    }
                }.start()
            }
            .show()
    }

    inner class ArkaNative {
        @JavascriptInterface
        fun pickFolder() {
            ui.post { launchTreePicker() }
        }
    }

    private fun launchTreePicker() {
        val i = Intent(Intent.ACTION_OPEN_DOCUMENT_TREE)
        i.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION)
        startActivityForResult(i, REQ_TREE)
    }

    @Deprecated("legacy picker")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_TREE || resultCode != RESULT_OK) return
        val uri = data?.data ?: return
        try {
            contentResolver.takePersistableUriPermission(
                uri,
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION
            )
        } catch (_: Exception) {}
        val path = treeUriToPath(uri)
        if (path.isNullOrBlank()) {
            Toast.makeText(this, "Folder itu tidak bisa dipakai agen. Pakai folder internal.", Toast.LENGTH_LONG).show()
            folderField?.setText(defaultWs)
            return
        }
        File(path).mkdirs()
        folderField?.setText(path)
        web.evaluateJavascript(
            "(function(){var e=document.getElementById('cfgWorkspace'); if(e) e.value=" +
                JSONObject.quote(path) + ";})();",
            null
        )
    }

    private fun treeUriToPath(uri: Uri): String? {
        return try {
            val id = DocumentsContract.getTreeDocumentId(uri)
            val parts = id.split(":", limit = 2)
            if (parts[0] == "primary") {
                val rest = if (parts.size > 1) parts[1] else ""
                if (rest.isEmpty()) "/storage/emulated/0" else "/storage/emulated/0/$rest"
            } else null
        } catch (_: Exception) {
            null
        }
    }

    companion object {
        private const val REQ_TREE = 71
        private const val FIT_JS = """
            (function(){
              var s=document.createElement('style');
              s.textContent='#app{grid-template-columns:1fr!important;height:100%!important;} html,body{height:100%;margin:0;overflow:hidden;} .gutter,.files{display:none!important;} .main{min-height:100%;} .sheet.scrollable{max-height:85vh;overflow-y:auto;}';
              document.documentElement.appendChild(s);
            })();
        """
    }
}
