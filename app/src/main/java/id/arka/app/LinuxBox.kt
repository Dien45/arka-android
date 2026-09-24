package id.arka.app

import android.content.Context
import java.io.File

/**
 * Linux di sini alat Arka, bukan aplikasi. v1: shell Android.
 * Alpine/proot (pola Acode) menyusul tanpa mengubah UI Arka.
 */
object LinuxBox {
    fun prepare(ctx: Context) {
        File(ctx.filesDir, "proyek").mkdirs()
        val wrap = bashWrapper(ctx) ?: return
        if (!wrap.exists()) {
            wrap.parentFile?.mkdirs()
            wrap.writeText(
                "#!/system/bin/sh\n" +
                    "if [ \"\$1\" = \"-lc\" ] || [ \"\$1\" = \"-c\" ]; then shift; exec /system/bin/sh -c \"\$*\"; fi\n" +
                    "exec /system/bin/sh \"\$@\"\n"
            )
            wrap.setExecutable(true, false)
        }
    }

    fun bashWrapper(ctx: Context): File? {
        return File(ctx.filesDir, "linux/arka-sh")
    }
}
