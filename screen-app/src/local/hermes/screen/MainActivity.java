package local.hermes.screen;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

public class MainActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        startForegroundService(new Intent(this, DisplayService.class).setAction("show"));
        finish();
    }
}
