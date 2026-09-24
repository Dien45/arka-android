package com.termux.app;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.graphics.Typeface;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.appcompat.app.AppCompatActivity;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Map;
import java.util.zip.ZipEntry;
import java.util.zip.ZipInputStream;

public class ArkaChatActivity extends AppCompatActivity {

    private static Process sServer;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private WebView web;
    private TextView status;
    private ProgressBar spin;
    private Button retry;
    private LinearLayout boot;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.parseColor("#101114"));

        web = new WebView(this);
        web.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(true);
        web.setWebViewClient(new WebViewClient());
        web.setVisibility(View.GONE);
        root.addView(web);

        boot = new LinearLayout(this);
        boot.setOrientation(LinearLayout.VERTICAL);
        boot.setGravity(Gravity.CENTER);
        boot.setLayoutParams(new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        boot.setPadding(40, 40, 40, 40);

        TextView brand = new TextView(this);
        brand.setText("Arka");
        brand.setTextColor(Color.parseColor("#ff9a62"));
        brand.setTextSize(28);
        brand.setTypeface(Typeface.DEFAULT_BOLD);
        brand.setGravity(Gravity.CENTER);
        boot.addView(brand);

        spin = new ProgressBar(this);
        LinearLayout.LayoutParams sp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        sp.gravity = Gravity.CENTER_HORIZONTAL;
        sp.topMargin = 24;
        spin.setLayoutParams(sp);
        boot.addView(spin);

        status = new TextView(this);
        status.setText("Menyiapkan Arka…");
        status.setTextColor(Color.parseColor("#c5c9d3"));
        status.setTextSize(15);
        status.setGravity(Gravity.CENTER);
        status.setPadding(0, 20, 0, 12);
        boot.addView(status);

        retry = new Button(this);
        retry.setText("Siapkan mesin (sekali)");
        retry.setVisibility(View.GONE);
        retry.setOnClickListener(v -> {
            Intent i = new Intent(this, TermuxActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
            startActivity(i);
        });
        boot.addView(retry);
        root.addView(boot);

        root.addView(ArkaTabs.bar(this, false));
        setContentView(root);

        extractPayload();
        startArka();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (web.getVisibility() != View.VISIBLE) startArka();
    }

    private File homeDir() {
        return new File(getApplicationInfo().dataDir, "home");
    }

    private File prefixDir() {
        return new File(getApplicationInfo().dataDir, "usr");
    }

    private void extractPayload() {
        File home = homeDir();
        File dest = new File(home, "arka");
        home.mkdirs();
        if (!new File(dest, "app/main.py").exists()) {
            try (InputStream in = getAssets().open("arka.zip");
                 ZipInputStream zis = new ZipInputStream(in)) {
                ZipEntry e;
                byte[] buf = new byte[8192];
                while ((e = zis.getNextEntry()) != null) {
                    File out = new File(home, e.getName());
                    if (e.isDirectory()) {
                        out.mkdirs();
                        continue;
                    }
                    File parent = out.getParentFile();
                    if (parent != null) parent.mkdirs();
                    try (FileOutputStream fos = new FileOutputStream(out)) {
                        int n;
                        while ((n = zis.read(buf)) > 0) fos.write(buf, 0, n);
                    }
                }
            } catch (Exception ignored) {}
        }
        File sh = new File(home, "start-arka.sh");
        String script = "#!/data/data/com.termux/files/usr/bin/bash\n"
            + "set -e\n"
            + "pkg update -y\n"
            + "pkg install -y python\n"
            + "pip install fastapi uvicorn httpx python-multipart\n"
            + "mkdir -p \"$HOME/proyek\"\n"
            + "cd \"$HOME/arka\"\n"
            + "exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8765\n";
        try (FileOutputStream fos = new FileOutputStream(sh)) {
            fos.write(script.getBytes());
        } catch (Exception ignored) {}
    }

    private void setStatus(String t, boolean showRetry) {
        ui.post(() -> {
            status.setText(t);
            retry.setVisibility(showRetry ? View.VISIBLE : View.GONE);
            spin.setVisibility(showRetry ? View.GONE : View.VISIBLE);
        });
    }

    private void showChat() {
        ui.post(() -> {
            boot.setVisibility(View.GONE);
            web.setVisibility(View.VISIBLE);
            web.loadUrl("http://127.0.0.1:8765");
        });
    }

    private boolean serverUp() {
        HttpURLConnection c = null;
        try {
            c = (HttpURLConnection) new URL("http://127.0.0.1:8765/api/health").openConnection();
            c.setConnectTimeout(800);
            c.setReadTimeout(800);
            c.connect();
            return c.getResponseCode() < 500;
        } catch (Exception e) {
            return false;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private void startArka() {
        new Thread(() -> {
            if (serverUp()) {
                showChat();
                return;
            }
            File bash = new File(prefixDir(), "bin/bash");
            File python = new File(prefixDir(), "bin/python");
            if (!bash.exists()) {
                setStatus("Mesin Linux belum siap. Ketuk tombol, tunggu bootstrap Termux selesai, lalu kembali ke tab Arka. Tidak perlu ketik perintah.", true);
                return;
            }
            extractPayload();
            if (!new File(homeDir(), "arka/app/main.py").exists()) {
                setStatus("Kode Arka belum terpasang di HP. Build APK ulang dari GitHub (payload/arka harus ada).", true);
                return;
            }
            setStatus(python.exists() ? "Menyalakan Arka…" : "Menginstal Python (sekali, butuh internet)…", false);
            try {
                if (sServer != null) {
                    sServer.destroy();
                    sServer = null;
                }
                String cmd = python.exists()
                    ? "mkdir -p \"$HOME/proyek\"; cd \"$HOME/arka\" && python -m uvicorn app.main:app --host 127.0.0.1 --port 8765"
                    : "pkg update -y && pkg install -y python && pip install fastapi uvicorn httpx python-multipart && mkdir -p \"$HOME/proyek\" && cd \"$HOME/arka\" && python -m uvicorn app.main:app --host 127.0.0.1 --port 8765";
                sServer = termuxBash(cmd);
                for (int i = 0; i < 90; i++) {
                    if (serverUp()) {
                        showChat();
                        return;
                    }
                    Thread.sleep(1000);
                    if (i == 8) setStatus("Masih menyiapkan…", false);
                }
                setStatus("Gagal menyala. Buka tab Terminal, pastikan internet hidup, lalu kembali.", true);
            } catch (Exception e) {
                setStatus("Error: " + e.getMessage(), true);
            }
        }).start();
    }

    private Process termuxBash(String command) throws Exception {
        File bash = new File(prefixDir(), "bin/bash");
        ProcessBuilder pb = new ProcessBuilder(bash.getAbsolutePath(), "-lc", command);
        pb.directory(homeDir());
        Map<String, String> env = pb.environment();
        String prefix = prefixDir().getAbsolutePath();
        String home = homeDir().getAbsolutePath();
        env.put("PREFIX", prefix);
        env.put("HOME", home);
        env.put("TMPDIR", prefix + "/tmp");
        env.put("PATH", prefix + "/bin:" + prefix + "/bin/applets");
        env.put("LD_LIBRARY_PATH", prefix + "/lib");
        env.put("ANDROID_ROOT", "/system");
        env.put("ANDROID_DATA", "/data");
        pb.redirectErrorStream(true);
        Process p = pb.start();
        new Thread(() -> {
            try (BufferedReader r = new BufferedReader(new InputStreamReader(p.getInputStream()))) {
                while (r.readLine() != null) { /* drain */ }
            } catch (Exception ignored) {}
        }).start();
        return p;
    }
}

class ArkaTabs {
    static LinearLayout bar(Activity a, boolean onTerminal) {
        LinearLayout bar = new LinearLayout(a);
        bar.setOrientation(LinearLayout.HORIZONTAL);
        bar.setBackgroundColor(Color.parseColor("#16171c"));
        bar.setPadding(8, 8, 8, 8);
        Button chat = tab(a, "Arka", !onTerminal);
        Button term = tab(a, "Terminal", onTerminal);
        chat.setOnClickListener(v -> {
            Intent i = new Intent(a, ArkaChatActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
            a.startActivity(i);
        });
        term.setOnClickListener(v -> {
            Intent i = new Intent(a, TermuxActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_REORDER_TO_FRONT);
            a.startActivity(i);
        });
        bar.addView(chat);
        bar.addView(term);
        return bar;
    }

    static void attachTo(Activity a, boolean onTerminal) {
        ViewGroup content = a.findViewById(android.R.id.content);
        if (content == null) return;
        LinearLayout bar = bar(a, onTerminal);
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT,
            ViewGroup.LayoutParams.WRAP_CONTENT,
            Gravity.BOTTOM
        );
        content.addView(bar, lp);
    }

    private static Button tab(Activity a, String label, boolean on) {
        Button b = new Button(a);
        b.setText(label);
        b.setAllCaps(false);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        lp.setMargins(6, 0, 6, 0);
        b.setLayoutParams(lp);
        b.setBackgroundColor(on ? Color.parseColor("#2a2d36") : Color.parseColor("#121318"));
        b.setTextColor(on ? Color.parseColor("#ff9a62") : Color.parseColor("#8b909d"));
        return b;
    }
}
