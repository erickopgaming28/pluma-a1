package com.plumaa1.app;

import android.app.Activity;
import android.content.ContentValues;
import android.content.Intent;
import android.content.res.AssetManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Environment;
import android.provider.MediaStore;
import android.text.TextUtils;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Toast;

import com.chaquo.python.Python;
import com.chaquo.python.android.AndroidPlatform;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Pluma A1 para Android: arranca dentro del teléfono el mismo motor de Python que usa la
 * app de PC y muestra su interfaz web en un WebView.
 */
public class MainActivity extends Activity {
    private static final String HOME = "http://127.0.0.1:8765/";
    private static final int PICK_IMAGE = 1;

    // el motor vive mientras viva el proceso, aunque la pantalla se vuelva a crear
    private static boolean engineStarted;
    private static volatile String engineError;

    private WebView web;
    private ValueCallback<Uri[]> fileCallback;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        web = new WebView(this);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        web.setWebViewClient(new WebViewClient());
        web.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback, FileChooserParams params) {
                if (fileCallback != null) fileCallback.onReceiveValue(null);
                fileCallback = callback;
                Intent pick = new Intent(Intent.ACTION_GET_CONTENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("image/*");
                startActivityForResult(Intent.createChooser(pick, "Elige una imagen"), PICK_IMAGE);
                return true;
            }
        });
        web.setDownloadListener((url, userAgent, disposition, mime, length) -> download(url));

        showMessage("Iniciando Pluma A1…", "La primera vez tarda un poco más.");
        new Thread(this::boot).start();
    }

    /** Copia interfaz y fuentes a disco, arranca el motor y abre la interfaz cuando responde. */
    private void boot() {
        File base = new File(getFilesDir(), "pluma");
        try {
            copyAssets("static", new File(base, "static"));
            copyAssets("fonts", new File(base, "fonts"));
        } catch (IOException e) {
            fail(e);
            return;
        }
        synchronized (MainActivity.class) {
            if (!engineStarted) {
                engineStarted = true;
                if (!Python.isStarted()) Python.start(new AndroidPlatform(getApplicationContext()));
                new Thread(() -> {
                    try {
                        Python.getInstance().getModule("main_android").callAttr("run", base.getAbsolutePath());
                        engineError = "El motor se detuvo.";
                    } catch (Throwable t) {
                        StringWriter w = new StringWriter();
                        t.printStackTrace(new PrintWriter(w));
                        engineError = w.toString();
                    }
                }, "motor").start();
            }
        }
        for (int i = 0; i < 240 && engineError == null; i++) {
            if (serverUp()) {
                runOnUiThread(() -> web.loadUrl(HOME));
                return;
            }
            try {
                Thread.sleep(500);
            } catch (InterruptedException e) {
                return;
            }
        }
        String err = engineError != null ? engineError : "El motor no respondió a tiempo.";
        runOnUiThread(() -> showMessage("No pude iniciar el motor", err));
    }

    private boolean serverUp() {
        try {
            HttpURLConnection c = (HttpURLConnection) new URL(HOME + "manifest.webmanifest").openConnection();
            c.setConnectTimeout(400);
            c.setReadTimeout(2000);
            int code = c.getResponseCode();
            c.disconnect();
            return code == 200;
        } catch (IOException e) {
            return false;
        }
    }

    private void copyAssets(String path, File target) throws IOException {
        AssetManager am = getAssets();
        String[] children = am.list(path);
        if (children == null || children.length == 0) {
            File parent = target.getParentFile();
            if (parent != null) parent.mkdirs();
            try (InputStream in = am.open(path); OutputStream out = new FileOutputStream(target)) {
                pipe(in, out);
            }
            return;
        }
        for (String child : children) copyAssets(path + "/" + child, new File(target, child));
    }

    private static void pipe(InputStream in, OutputStream out) throws IOException {
        byte[] buf = new byte[16384];
        for (int n; (n = in.read(buf)) > 0; ) out.write(buf, 0, n);
    }

    /** "Descargar archivo" de la interfaz: guarda el .gcode.3mf en Descargas. */
    private void download(String url) {
        new Thread(() -> {
            try {
                HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
                if (c.getResponseCode() != 200) throw new IOException("HTTP " + c.getResponseCode());
                String cd = c.getHeaderField("Content-Disposition");
                Matcher m = Pattern.compile("filename=\"?([^\";]+)").matcher(cd == null ? "" : cd);
                String name = m.find() ? m.group(1) : "pluma.gcode.3mf";
                OutputStream out;
                if (Build.VERSION.SDK_INT >= 29) {
                    ContentValues v = new ContentValues();
                    v.put(MediaStore.Downloads.DISPLAY_NAME, name);
                    v.put(MediaStore.Downloads.MIME_TYPE, "application/octet-stream");
                    Uri dest = getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, v);
                    if (dest == null) throw new IOException("sin acceso a Descargas");
                    out = getContentResolver().openOutputStream(dest);
                } else {
                    out = new FileOutputStream(new File(getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), name));
                }
                try (InputStream in = c.getInputStream(); OutputStream o = out) {
                    pipe(in, o);
                }
                toast("Guardado en Descargas: " + name);
            } catch (Exception e) {
                toast("No pude guardar el archivo: " + e.getMessage());
            }
        }).start();
    }

    private void toast(String text) {
        runOnUiThread(() -> Toast.makeText(this, text, Toast.LENGTH_LONG).show());
    }

    private void fail(Throwable t) {
        runOnUiThread(() -> showMessage("No pude preparar la app", String.valueOf(t)));
    }

    private void showMessage(String title, String detail) {
        String html = "<meta name=viewport content='width=device-width'>"
                + "<body style='font-family:sans-serif;background:#f1ede4;color:#23242a;padding:28px'>"
                + "<h3>" + TextUtils.htmlEncode(title) + "</h3>"
                + "<pre style='white-space:pre-wrap;font-size:12px'>" + TextUtils.htmlEncode(detail) + "</pre>";
        web.loadDataWithBaseURL(null, html, "text/html", "utf-8", null);
    }

    @Override
    protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != PICK_IMAGE || fileCallback == null) return;
        Uri picked = result == RESULT_OK && data != null ? data.getData() : null;
        fileCallback.onReceiveValue(picked == null ? null : new Uri[]{picked});
        fileCallback = null;
    }

    @Override
    public void onBackPressed() {
        // atrás no cierra la app a media carta: la manda a segundo plano
        moveTaskToBack(true);
    }
}
