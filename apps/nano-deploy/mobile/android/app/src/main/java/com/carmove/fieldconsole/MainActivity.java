package com.carmove.fieldconsole;

import android.app.Activity;
import android.content.SharedPreferences;
import android.graphics.Color;
import android.graphics.Typeface;
import android.net.Uri;
import android.os.Bundle;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.util.Locale;

public class MainActivity extends Activity {
    private static final String PREFS = "carmove_field_console";
    private static final String DASHBOARD_URL = "dashboard_url";
    private static final int INK = Color.rgb(232, 239, 233);
    private static final int MUTED = Color.rgb(142, 163, 155);
    private static final int PAGE = Color.rgb(9, 17, 15);
    private static final int PANEL = Color.rgb(16, 28, 24);
    private static final int ACID = Color.rgb(217, 255, 87);

    private SharedPreferences preferences;
    private WebView webView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        requestWindowFeature(Window.FEATURE_NO_TITLE);
        preferences = getSharedPreferences(PREFS, MODE_PRIVATE);
        String savedUrl = preferences.getString(DASHBOARD_URL, "");
        if (savedUrl.isEmpty()) {
            showConfiguration("");
        } else {
            showConsole(savedUrl);
        }
    }

    private int dp(float value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private TextView text(String value, float size, int color) {
        TextView view = new TextView(this);
        view.setText(value);
        view.setTextSize(size);
        view.setTextColor(color);
        return view;
    }

    private LinearLayout baseLayout() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(dp(22), dp(24), dp(22), dp(20));
        root.setBackgroundColor(PAGE);
        return root;
    }

    private void showConfiguration(String existingUrl) {
        LinearLayout root = baseLayout();

        TextView eyebrow = text("CARMOVE / ANDROID SETUP", 11, ACID);
        eyebrow.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        root.addView(eyebrow, new LinearLayout.LayoutParams(-1, -2));

        TextView title = text("连接现场控制台", 30, INK);
        title.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        LinearLayout.LayoutParams titleParams = new LinearLayout.LayoutParams(-1, -2);
        titleParams.setMargins(0, dp(16), 0, dp(10));
        root.addView(title, titleParams);

        TextView help = text("先在手机上打开 Tailscale 并登录同一个 tailnet，再填写 Dashboard 的地址。", 14, MUTED);
        help.setLineSpacing(0, 1.25f);
        root.addView(help, new LinearLayout.LayoutParams(-1, -2));

        EditText urlInput = new EditText(this);
        urlInput.setSingleLine(true);
        urlInput.setText(existingUrl);
        urlInput.setHint("例如 http://100.64.0.10:8080");
        urlInput.setHintTextColor(Color.rgb(96, 118, 109));
        urlInput.setTextColor(INK);
        urlInput.setTextSize(14);
        urlInput.setPadding(dp(13), 0, dp(13), 0);
        LinearLayout.LayoutParams inputParams = new LinearLayout.LayoutParams(-1, dp(50));
        inputParams.setMargins(0, dp(26), 0, dp(12));
        root.addView(urlInput, inputParams);

        Button openButton = new Button(this);
        openButton.setText("打开 CarMove 控制台");
        openButton.setTextColor(Color.rgb(23, 32, 13));
        openButton.setTextSize(14);
        openButton.setAllCaps(false);
        openButton.setBackgroundColor(ACID);
        openButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                String normalized = normalizeUrl(urlInput.getText().toString());
                if (normalized.isEmpty()) {
                    urlInput.setError("请填写 Dashboard 地址");
                    return;
                }
                preferences.edit().putString(DASHBOARD_URL, normalized).apply();
                showConsole(normalized);
            }
        });
        root.addView(openButton, new LinearLayout.LayoutParams(-1, dp(50)));

        TextView note = text("APK 只负责打开控制台；Tailscale 负责手机到 Dashboard 的私网连接。", 11, MUTED);
        note.setLineSpacing(0, 1.3f);
        LinearLayout.LayoutParams noteParams = new LinearLayout.LayoutParams(-1, -2);
        noteParams.setMargins(0, dp(24), 0, 0);
        root.addView(note, noteParams);

        setContentView(root);
    }

    private void showConsole(String dashboardUrl) {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(PAGE);

        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(12), 0, dp(8), 0);
        toolbar.setBackgroundColor(PANEL);
        TextView label = text("CarMove Field Console", 14, INK);
        label.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        toolbar.addView(label, new LinearLayout.LayoutParams(0, dp(48), 1));

        Button changeButton = new Button(this);
        changeButton.setText("地址");
        changeButton.setTextColor(ACID);
        changeButton.setTextSize(12);
        changeButton.setAllCaps(false);
        changeButton.setBackgroundColor(Color.TRANSPARENT);
        changeButton.setOnClickListener(new View.OnClickListener() {
            @Override
            public void onClick(View view) {
                showConfiguration(preferences.getString(DASHBOARD_URL, dashboardUrl));
            }
        });
        toolbar.addView(changeButton, new LinearLayout.LayoutParams(dp(60), dp(44)));
        root.addView(toolbar, new LinearLayout.LayoutParams(-1, dp(48)));

        webView = new WebView(this);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setMediaPlaybackRequiresUserGesture(false);
        settings.setLoadWithOverviewMode(false);
        settings.setUseWideViewPort(false);
        webView.setWebViewClient(new WebViewClient());
        webView.setWebChromeClient(new WebChromeClient());
        webView.setBackgroundColor(PAGE);
        webView.loadUrl(dashboardUrl);
        root.addView(webView, new LinearLayout.LayoutParams(-1, 0, 1));

        setContentView(root);
    }

    private String normalizeUrl(String raw) {
        String value = raw.trim();
        if (value.isEmpty()) return "";
        if (!value.toLowerCase(Locale.US).startsWith("http://") && !value.toLowerCase(Locale.US).startsWith("https://")) {
            value = "http://" + value;
        }
        Uri parsed = Uri.parse(value);
        if (parsed.getHost() == null) return "";
        return value.replaceAll("/+$", "");
    }

    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            webView.goBack();
        } else {
            super.onBackPressed();
        }
    }
}
