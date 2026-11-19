package io.github.arnavdotexe.filmsim.app

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import io.github.arnavdotexe.filmsim.look.LookLibrary
import io.github.arnavdotexe.filmsim.look.LookSource

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme(colorScheme = darkColorScheme()) { App() }
        }
    }
}

/** The bundled looks from assets/looks. */
fun loadBundledLooks(context: Context): LookLibrary {
    val assets = context.assets
    val files = assets.list("looks").orEmpty().filter { it.endsWith(".fsim.json") }
    return LookLibrary.load(files.map { name -> LookSource(name, assets.open("looks/$name").bufferedReader().use { it.readText() }) })
}

@Composable
private fun App() {
    val context = LocalContext.current
    var granted by remember {
        mutableStateOf(ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED)
    }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { granted = it }
    LaunchedEffect(Unit) { if (!granted) ask.launch(Manifest.permission.CAMERA) }
    Box(Modifier.fillMaxSize().background(Color.Black)) {
        if (granted) {
            CameraScreen()
        } else {
            Text(
                "Emulsion needs the camera to take photos. Tap to allow.",
                color = Color.White,
                modifier = Modifier.align(Alignment.Center).clickable { ask.launch(Manifest.permission.CAMERA) }.padding(24.dp),
            )
        }
    }
}
