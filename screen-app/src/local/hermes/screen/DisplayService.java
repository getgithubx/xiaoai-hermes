package local.hermes.screen;

import android.app.*;
import android.content.*;
import android.graphics.Color;
import android.os.*;
import android.provider.Settings;
import android.util.Base64;
import android.view.*;
import android.widget.*;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;

public class DisplayService extends Service {
    private WindowManager windows;
    private LinearLayout panel;
    private TextView status, question, answer;
    private ScrollView scroll;
    private String query = "", reply = "说“小爱同学”唤醒，然后直接提问。\nHermes 的回答会显示在这里。", phase = "已就绪";
    private SharedPreferences saved;
    private int dp(int value) { return (int)(value * getResources().getDisplayMetrics().density + .5f); }
    private TextView text(int sp, String color) {
        TextView view = new TextView(this);
        view.setTextSize(sp); view.setTextColor(Color.parseColor(color));
        view.setLineSpacing(dp(4), 1); return view;
    }
    @Override public void onCreate() {
        super.onCreate();
        saved = getSharedPreferences("display", MODE_PRIVATE);
        query = saved.getString("query", query); reply = saved.getString("reply", reply);
        phase = saved.getString("phase", phase);
        windows = (WindowManager)getSystemService(WINDOW_SERVICE);
        NotificationManager manager = (NotificationManager)getSystemService(NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel("display", "Hermes 屏显", NotificationManager.IMPORTANCE_LOW));
        PendingIntent open = PendingIntent.getActivity(this, 0, new Intent(this, MainActivity.class), PendingIntent.FLAG_UPDATE_CURRENT);
        startForeground(1, new Notification.Builder(this, "display").setContentTitle("Hermes 屏显")
            .setContentText("等待对话 · 点按查看最近回答").setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(open).setOngoing(true).build());
    }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent != null) {
            String payload = intent.getStringExtra("payload");
            if (payload != null) {
                try {
                    JSONObject data = new JSONObject(new String(Base64.decode(payload, Base64.DEFAULT), StandardCharsets.UTF_8));
                    query = data.optString("question"); reply = data.optString("answer");
                    phase = data.optString("status", "Hermes");
                    saved.edit().putString("query", query).putString("reply", reply).putString("phase", phase).apply();
                    show();
                } catch (Exception ignored) { android.util.Log.w("HermesScreen", "Invalid display payload"); }
            } else if ("show".equals(intent.getAction())) show();
        }
        return START_STICKY;
    }
    private void show() {
        if (!Settings.canDrawOverlays(this)) return;
        if (panel == null) {
            panel = new LinearLayout(this); panel.setOrientation(LinearLayout.VERTICAL);
            panel.setBackgroundColor(Color.parseColor("#111820")); panel.setPadding(dp(20), dp(10), dp(20), dp(12));
            LinearLayout header = new LinearLayout(this); header.setGravity(Gravity.CENTER_VERTICAL);
            TextView brand = text(20, "#78DDCC"); brand.setText("HERMES");
            header.addView(brand, new LinearLayout.LayoutParams(0, dp(42), 1));
            status = text(12, "#AABBCB"); header.addView(status);
            Button close = new Button(this); close.setText("收起"); close.setTextSize(13);
            close.setOnClickListener(v -> hide()); header.addView(close, new LinearLayout.LayoutParams(dp(72), dp(42)));
            panel.addView(header);
            scroll = new ScrollView(this); scroll.setFillViewport(true);
            LinearLayout content = new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL);
            question = text(14, "#AABBCB"); question.setPadding(0, dp(4), 0, dp(10)); content.addView(question);
            answer = text(20, "#F2F5F8"); answer.setPadding(0, 0, 0, dp(16)); content.addView(answer);
            scroll.addView(content); panel.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1));
            TextView hint = text(11, "#798B9D"); hint.setText("上下滑动阅读  ·  新对话自动显示"); panel.addView(hint);
            WindowManager.LayoutParams params = new WindowManager.LayoutParams(-1, -1,
                WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
                WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE | WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
                android.graphics.PixelFormat.OPAQUE);
            params.gravity = Gravity.TOP | Gravity.LEFT;
            windows.addView(panel, params);
        }
        status.setText(phase + "  "); question.setText(query.isEmpty() ? "你好，开始对话吧" : "你：" + query);
        answer.setText(reply); scroll.post(() -> scroll.scrollTo(0, 0));
    }
    private void hide() { if (panel != null) { windows.removeView(panel); panel = null; } }
    @Override public void onDestroy() { hide(); super.onDestroy(); }
    @Override public IBinder onBind(Intent intent) { return null; }
}
