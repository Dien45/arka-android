package id.arka.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.os.Build
import android.os.IBinder
import com.chaquo.python.Python
import java.io.File

class ArkaService : Service() {
    @Volatile private var started = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ensureChannel()
        startForeground(1, note("Arka menyala…"))
        LinuxBox.prepare(this)
        Thread({ runServer() }, "arka-python").start()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        return START_STICKY
    }

    private fun runServer() {
        if (started) return
        started = true
        try {
            val home = File(filesDir, ".arka")
            val proyek = File(filesDir, "proyek")
            home.mkdirs()
            proyek.mkdirs()
            val py = Python.getInstance()
            py.getModule("arka_boot").callAttr(
                "start_server",
                home.absolutePath,
                proyek.absolutePath
            )
        } catch (e: Exception) {
            started = false
            e.printStackTrace()
        }
    }

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(NotificationManager::class.java)
        nm.createNotificationChannel(
            NotificationChannel("arka", "Arka", NotificationManager.IMPORTANCE_LOW)
        )
    }

    private fun note(text: String): Notification {
        val b = if (Build.VERSION.SDK_INT >= 26) {
            Notification.Builder(this, "arka")
        } else {
            @Suppress("DEPRECATION")
            Notification.Builder(this)
        }
        return b.setContentTitle("Arka")
            .setContentText(text)
            .setSmallIcon(R.drawable.ic_arka)
            .setOngoing(true)
            .build()
    }
}
